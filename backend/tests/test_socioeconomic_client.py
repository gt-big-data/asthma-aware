import pytest

from ingestion.socioeconomic import client


def test_missing_component_does_not_become_a_partial_population_total():
    assert client._sum_values(10, None, 20) is None
    assert client._sum_values(10, 0, 20) == 30


def test_housing_age_is_tied_to_the_dataset_vintage():
    assert client._median_housing_age(2000) == client.ACS_DATASET_YEAR - 2000
    assert client._median_housing_age(None) is None


def test_fetch_requires_credentials_without_silently_returning_sample_data(monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(client.CensusError, match="CENSUS_API_KEY"):
        client.fetch_rows()
