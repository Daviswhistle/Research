from distressed_equity.sec_cik_lookup import (
    SEC_CIK_LOOKUP_URL,
    fetch_cik_lookup_matches,
    parse_cik_lookup_matches,
)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def get(self, url, **kwargs):
        assert url == SEC_CIK_LOOKUP_URL
        self.calls += 1
        return FakeResponse(self.text)


class FakeClient:
    user_agent = "Research test@example.com"

    def __init__(self, text):
        self.session = FakeSession(text)

    def _throttle(self):
        return None


def test_cik_lookup_parser_splits_from_right_when_entity_name_contains_colon():
    payload = (
        "11:11 CAPITAL CORP.:0001463262:\n"
        "TARGET FINANCE LLC:0002222222:\n"
        "TARGET FINANCE LLC:0003333333:\n"
    )
    matches = parse_cik_lookup_matches(
        payload,
        ("11 11 capital corp", "target finance llc"),
    )
    assert [(item.name, item.cik) for item in matches["11 11 capital corp"]] == [
        ("11:11 CAPITAL CORP.", "0001463262")
    ]
    assert {item.cik for item in matches["target finance llc"]} == {
        "0002222222",
        "0003333333",
    }


def test_cik_lookup_fetch_is_cached_on_client_and_filters_requested_names():
    client = FakeClient(
        "TARGET FINANCE LLC:0002222222:\n"
        "OTHER COMPANY INC.:0004444444:\n"
    )
    first = fetch_cik_lookup_matches(client, ("target finance llc",))
    second = fetch_cik_lookup_matches(client, ("other company inc",))
    assert client.session.calls == 1
    assert [item.cik for item in first["target finance llc"]] == ["0002222222"]
    assert [item.cik for item in second["other company inc"]] == ["0004444444"]
