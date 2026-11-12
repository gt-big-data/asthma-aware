"""Check independence and per-source shapes without assuming shared dates."""

import io
import unittest
from dataclasses import asdict

import torch

from asthma_aware import ResidualUNet, SourceModelConfig, build_source_models


class SourceModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(11)

    def config(self, **overrides):
        settings = dict(
            forecast_channels=3,
            target_names=("no2_column",),
            encoder_channels=(4, 8),
            forecast_encoder_channels=(4,),
            head_channels=4,
        )
        settings.update(overrides)
        return SourceModelConfig(**settings)

    def test_sources_accept_different_inputs_grids_batches_and_targets(self):
        models = build_source_models({
            "no2": self.config(),
            "o3": self.config(forecast_channels=5, target_names=("o3_column",), time_channels=2),
            "weather": self.config(forecast_channels=2, geography_channels=1,
                                   target_names=("temperature_c", "dewpoint_c")),
        })
        no2_baseline = torch.randn(1, 1, 17, 23)
        no2 = models["no2"](torch.randn(1, 3, 3, 4), baseline=no2_baseline)
        o3_baseline = torch.randn(2, 1, 19, 31)
        o3 = models["o3"](torch.randn(2, 5, 2, 5), baseline=o3_baseline,
                           time_features=torch.randn(2, 2))
        weather_baseline = torch.randn(3, 2, 12, 14)
        weather = models["weather"](torch.randn(3, 2, 2, 2), torch.randn(3, 1, 12, 14),
                                     weather_baseline)
        for prediction, baseline in ((no2, no2_baseline), (o3, o3_baseline),
                                     (weather, weather_baseline)):
            torch.testing.assert_close(prediction, baseline, rtol=0, atol=0)
        # Models can be called independently, with no batch for the other sources.
        self.assertIsInstance(models["no2"], ResidualUNet)

    def test_training_one_source_leaves_other_source_unchanged(self):
        config = self.config()
        models = build_source_models({"source_a": config, "source_b": config})
        model_a, model_b = models.values()
        a_parameters = {id(parameter) for parameter in model_a.parameters()}
        b_parameters = {id(parameter) for parameter in model_b.parameters()}
        self.assertTrue(a_parameters.isdisjoint(b_parameters))
        before_b = {name: value.clone() for name, value in model_b.state_dict().items()}
        forecast = torch.randn(1, 3, 3, 4)
        baseline = torch.randn(1, 1, 17, 23)
        before_prediction_b = model_b(forecast, baseline=baseline).detach().clone()
        optimizer = torch.optim.SGD(model_a.parameters(), lr=0.05)
        loss = (model_a(forecast, baseline=baseline) - (baseline + 1)).square().mean()
        loss.backward()
        self.assertTrue(all(parameter.grad is None for parameter in model_b.parameters()))
        optimizer.step()
        self.assertFalse(torch.equal(model_a(forecast, baseline=baseline), baseline))
        for name, value in model_b.state_dict().items():
            torch.testing.assert_close(value, before_b[name], rtol=0, atol=0)
        torch.testing.assert_close(model_b(forecast, baseline=baseline), before_prediction_b,
                                   rtol=0, atol=0)

    def test_checkpoint_restores_source_configuration_and_predictions(self):
        config = self.config(time_channels=2, zero_init_residual=False)
        model = config.build_model().eval()
        forecast = torch.randn(1, 3, 3, 4)
        baseline = torch.randn(1, 1, 17, 23)
        time = torch.randn(1, 2)
        expected = model(forecast, baseline=baseline, time_features=time)
        checkpoint = io.BytesIO()
        torch.save({"source": "no2", "model_config": asdict(config),
                    "model_state_dict": model.state_dict()}, checkpoint)
        checkpoint.seek(0)
        saved = torch.load(checkpoint, weights_only=True)
        restored = SourceModelConfig(**saved["model_config"]).build_model().eval()
        restored.load_state_dict(saved["model_state_dict"])
        torch.testing.assert_close(restored(forecast, baseline=baseline, time_features=time),
                                   expected, rtol=0, atol=0)

    def test_invalid_source_configuration(self):
        for configs, exception in (({}, ValueError), ({"": self.config()}, ValueError),
                                    ({3: self.config()}, ValueError),
                                    ({"no2": {}}, TypeError)):
            with self.subTest(configs=configs), self.assertRaises(exception):
                build_source_models(configs)
        with self.assertRaisesRegex(ValueError, "forecast_channels"):
            build_source_models({"no2": self.config(forecast_channels=0)})


if __name__ == "__main__":
    unittest.main()
