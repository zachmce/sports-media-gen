"""ESPN schema drift detection tests (ESPN-03 / D-07).

These tests verify that strict Pydantic v2 models fail loudly when ESPN
returns a payload that is missing required fields the seed depends on.
"""

import pydantic
import pytest

from matchup_thumbs.espn.models import ESPNTeamsResponse


def test_espn_schema_drift_fails() -> None:
    """ESPN-03: malformed payload missing required team fields raises ValidationError.

    A payload where the inner ``team`` object has only ``id`` (missing slug,
    abbreviation, displayName, shortDisplayName, name, location) must raise
    ``pydantic.ValidationError`` at model_validate time.

    This is the field-presence drift detection contract: the seed depends on
    all required fields; if ESPN renames or drops any of them the failure is
    immediate and loud, not a silent bad upsert.
    """
    malformed = {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {
                                "team": {
                                    "id": "1"
                                    # missing: slug, abbreviation, displayName,
                                    # shortDisplayName, name, location
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with pytest.raises(pydantic.ValidationError):
        ESPNTeamsResponse.model_validate(malformed)


def test_espn_schema_empty_sports_raises_validation_error() -> None:
    """CR-01: empty sports array raises ValidationError at parse time (D-07).

    ESPNTeamsResponse.sports has min_length=1.  ESPN returning ``{"sports": []}``
    must raise pydantic.ValidationError rather than propagating an IndexError
    when seed code accesses sports[0].
    """
    with pytest.raises(pydantic.ValidationError):
        ESPNTeamsResponse.model_validate({"sports": []})


def test_espn_schema_empty_leagues_raises_validation_error() -> None:
    """CR-01: empty leagues array raises ValidationError at parse time (D-07).

    ESPNSport.leagues has min_length=1.  ESPN returning a sport with no leagues
    must raise pydantic.ValidationError rather than an IndexError at leagues[0].
    """
    with pytest.raises(pydantic.ValidationError):
        ESPNTeamsResponse.model_validate({"sports": [{"leagues": []}]})


def test_espn_schema_extra_fields_ignored() -> None:
    """ESPN-03 forward-compat: unknown extra fields on a well-formed team are tolerated.

    extra='ignore' means benign additive ESPN changes do NOT cause the seed to
    fail.  This test verifies an unknown extra field on an otherwise-valid team
    object does not raise and does not mask any required fields.
    """
    payload_with_extra = {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {
                                "team": {
                                    "id": "13",
                                    "slug": "los-angeles-lakers",
                                    "abbreviation": "LAL",
                                    "displayName": "Los Angeles Lakers",
                                    "shortDisplayName": "Lakers",
                                    "name": "Lakers",
                                    "location": "Los Angeles",
                                    "color": "552583",
                                    "unknownFutureField": "some-value",
                                    "anotherNewESPNField": {"nested": True},
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    response = ESPNTeamsResponse.model_validate(payload_with_extra)
    team = response.sports[0].leagues[0].teams[0].team
    assert team.slug == "los-angeles-lakers"
    assert team.abbreviation == "LAL"
    # The unknown field is not accessible (extra='ignore')
    assert not hasattr(team, "unknownFutureField")
