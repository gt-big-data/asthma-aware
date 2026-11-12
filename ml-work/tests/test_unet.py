"""Synthetic architecture checks; these do not validate downscaling accuracy."""

import unittest

import torch

from asthma_aware import ResidualUNet


class ResidualUNetTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)

    def model(self, targets=("no2", "pm25", "o3", "temperature", "humidity"), **kwargs):
        config = dict(
            forecast_channels=7,
            geography_channels=3,
            target_names=targets,
            encoder_channels=(4, 8, 16),
            forecast_encoder_channels=(4, 8),
            head_channels=4,
        )
        config.update(kwargs)
        return ResidualUNet(**config)

    def inputs(self, targets=5, height=31, width=45, batch=2):
        return (
            torch.randn(batch, 7, 5, 8),
            torch.randn(batch, 3, height, width),
            torch.randn(batch, targets, height, width),
        )

    def test_five_heads_preserve_baseline_at_initialization(self):
        model = self.model(time_channels=4)
        forecast, geography, baseline = self.inputs()
        prediction = model(forecast, geography, baseline, torch.randn(2, 4))
        self.assertEqual(prediction.shape, (2, 5, 31, 45))
        torch.testing.assert_close(prediction, baseline, rtol=0, atol=0)

    def test_single_target_and_runtime_spatial_sizes(self):
        model = self.model(targets=("tropospheric_no2",))
        for height, width in ((16, 20), (19, 27), (4, 4)):
            with self.subTest(height=height, width=width):
                prediction = model(*self.inputs(targets=1, height=height, width=width, batch=1))
                self.assertEqual(prediction.shape, (1, 1, height, width))

    def test_forecast_only_shapes_and_initial_baseline(self):
        for targets in (("no2",), ("no2", "pm25", "o3")):
            model = self.model(targets=targets, geography_channels=0, time_channels=2)
            for height, width in ((31, 45), (16, 20), (4, 4)):
                with self.subTest(targets=targets, height=height, width=width):
                    forecast = torch.randn(1, 7, 1, 3)
                    baseline = torch.randn(1, len(targets), height, width)
                    prediction = model(forecast, baseline=baseline, time_features=torch.randn(1, 2))
                    self.assertEqual(prediction.shape, baseline.shape)
                    torch.testing.assert_close(prediction, baseline, rtol=0, atol=0)

    def test_forecast_only_backpropagation_and_input_dependence(self):
        for time_channels in (0, 3):
            with self.subTest(time_channels=time_channels):
                model = self.model(geography_channels=0, time_channels=time_channels,
                                   zero_init_residual=False)
                forecast, _, baseline = self.inputs()
                forecast.requires_grad_()
                time = torch.randn(2, time_channels, requires_grad=True) if time_channels else None
                prediction = model(forecast, baseline=baseline, time_features=time)
                changed = model(forecast + torch.randn_like(forecast), baseline=baseline,
                                time_features=time)
                self.assertFalse(torch.allclose(prediction, changed))
                prediction.square().mean().backward()
                tensors = [forecast] if time is None else [forecast, time]
                for tensor in tensors:
                    self.assertIsNotNone(tensor.grad)
                    self.assertTrue(torch.isfinite(tensor.grad).all())
                    self.assertGreater(tensor.grad.abs().sum().item(), 0)
                for name, parameter in model.named_parameters():
                    with self.subTest(parameter=name):
                        self.assertIsNotNone(parameter.grad)
                        self.assertTrue(torch.isfinite(parameter.grad).all())
                        self.assertGreater(parameter.grad.abs().sum().item(), 0)

    def test_forecast_only_zero_initialized_heads_can_learn(self):
        model = self.model(geography_channels=0)
        forecast, _, baseline = self.inputs()
        optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
        prediction = model(forecast, baseline=baseline)
        (prediction - (baseline + 1)).square().mean().backward()
        optimizer.step()
        self.assertFalse(torch.equal(model(forecast, baseline=baseline), baseline))

    def test_forecast_only_invalid_inputs(self):
        model = self.model(geography_channels=0, time_channels=2)
        forecast, geography, baseline = self.inputs()
        time = torch.randn(2, 2)
        with self.assertRaisesRegex(ValueError, "baseline is required"):
            model(forecast, time_features=time)
        with self.assertRaisesRegex(ValueError, "geography must be omitted"):
            model(forecast, geography, baseline, time)
        with self.assertRaisesRegex(ValueError, "time_features must have shape"):
            model(forecast, baseline=baseline)
        cases = (
            (forecast[:, :6], baseline, time, "forecast must have shape"),
            (forecast[:1], baseline, time, "same batch size"),
            (forecast, baseline[:, :4], time, "baseline must have shape"),
            (forecast, baseline[:, :, :3], time, "must each be >= 4"),
            (forecast.long(), baseline, time, "must be floating point"),
            (forecast.double(), baseline, time, "same device and floating-point dtype"),
            (forecast, baseline, time.double(), "same device and floating-point dtype"),
        )
        for coarse, base, temporal, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                model(coarse, baseline=base, time_features=temporal)
        with self.assertRaisesRegex(ValueError, "geography is required"):
            self.model()(forecast, baseline=baseline)

    def test_encoder_depth_is_configurable(self):
        for widths in ((4, 8), (4, 8, 16, 32)):
            with self.subTest(widths=widths):
                model = ResidualUNet(2, 1, ("target",), encoder_channels=widths,
                                     forecast_encoder_channels=(4,), head_channels=2)
                prediction = model(torch.randn(1, 2, 1, 1), torch.randn(1, 1, 17, 25),
                                   torch.randn(1, 1, 17, 25))
                self.assertEqual(prediction.shape, (1, 1, 17, 25))

    def test_gradients_reach_both_branches_time_and_every_head(self):
        model = self.model(time_channels=3, zero_init_residual=False)
        forecast, geography, baseline = self.inputs()
        forecast.requires_grad_()
        geography.requires_grad_()
        time = torch.randn(2, 3, requires_grad=True)
        prediction = model(forecast, geography, baseline, time)
        loss = (prediction - torch.randn_like(prediction)).square().mean()
        loss.backward()
        for tensor in (forecast, geography, time):
            self.assertIsNotNone(tensor.grad)
            self.assertTrue(torch.isfinite(tensor.grad).all())
            self.assertGreater(tensor.grad.abs().sum().item(), 0)
        for name, parameter in model.named_parameters():
            with self.subTest(parameter=name):
                self.assertIsNotNone(parameter.grad)
                self.assertTrue(torch.isfinite(parameter.grad).all())
                self.assertGreater(parameter.grad.abs().sum().item(), 0)

    def test_zero_initialized_heads_can_learn(self):
        model = self.model()
        inputs = self.inputs()
        optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
        prediction = model(*inputs)
        loss = (prediction - (inputs[2] + 1)).square().mean()
        loss.backward()
        optimizer.step()
        self.assertFalse(torch.equal(model(*inputs), inputs[2]))

    def test_time_features_are_required_when_configured(self):
        model = self.model(time_channels=3)
        for time in (None, torch.randn(2, 2), torch.randn(1, 3)):
            with self.subTest(time=time):
                with self.assertRaisesRegex(ValueError, "time_features must have shape"):
                    model(*self.inputs(), time_features=time)
        with self.assertRaisesRegex(ValueError, "time_channels is zero"):
            self.model()(*self.inputs(), time_features=torch.randn(2, 1))

    def test_invalid_input_dimensions_raise_clear_errors(self):
        model = self.model()
        forecast, geography, baseline = self.inputs()
        cases = (
            ((forecast[:, :6], geography, baseline), "forecast must have shape"),
            ((forecast, geography[:, :2], baseline), "geography must have shape"),
            ((forecast, geography, baseline[:, :4]), "baseline must have shape"),
            ((forecast[:1], geography, baseline), "same batch size"),
            ((forecast, geography, baseline[:, :, :-1]), "match the geography output grid"),
            ((forecast, geography[:, :, :3], baseline[:, :, :3]), "must each be >= 4"),
            ((forecast.long(), geography, baseline), "must be floating point"),
            ((forecast.double(), geography, baseline), "same device and floating-point dtype"),
        )
        for inputs, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    model(*inputs)

    def test_invalid_model_configuration(self):
        cases = (
            {"forecast_channels": 0},
            {"geography_channels": -1},
            {"target_names": ()},
            {"target_names": "no2"},
            {"target_names": ("no2", "no2")},
            {"target_names": ("",)},
            {"encoder_channels": (4,)},
            {"encoder_channels": (1, 4)},
            {"forecast_encoder_channels": ()},
            {"head_channels": 0},
            {"time_channels": -1},
        )
        for override in cases:
            config = dict(forecast_channels=2, geography_channels=1, target_names=("target",))
            config.update(override)
            with self.subTest(override=override), self.assertRaises(ValueError):
                ResidualUNet(**config)


if __name__ == "__main__":
    unittest.main()
