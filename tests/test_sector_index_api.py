from app.hithink.api import HithinkClient


def test_sector_index_snapshot_batches_and_preserves_source_times():
    class Response:
        status_code = 200
        text = ""
        def __init__(self, codes, timestamp):
            self.codes = codes
            self.timestamp = timestamp

        def json(self):
            return {
                "code": 0,
                "data": {
                    "total": len(self.codes),
                    "timestamp": self.timestamp,
                    "item": [{"thscode": code} for code in self.codes],
                },
            }

    class Client:
        def get(self, url, params):
            codes = params["thscodes"].split(",")
            return Response(codes, 1_787_814_000_000 + len(codes))

    api = object.__new__(HithinkClient)
    api.client = Client()
    result = api.fetch_sector_index_snapshot(["A", "B", "C", "D", "E"], batch_size=2)

    assert result.total == 5
    assert [row.item["thscode"] for row in result.items] == ["A", "B", "C", "D", "E"]
    assert result.items[0].source_timestamp == 1_787_814_000_002
    assert result.items[-1].source_timestamp == 1_787_814_000_001
