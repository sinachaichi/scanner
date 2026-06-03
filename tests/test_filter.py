
from scanner.domain import ParsedConfig, ProbeResult
from scanner.ranker import filter_and_rank


def _make_result(host: str, speed: float, working: bool = True) -> ProbeResult:
    return ProbeResult(
        config=ParsedConfig(
            protocol='vless', raw_link=f'vless://uuid@{host}:443',
            host=host, port=443, user_id='uuid', method=None, remark='test',
        ),
        is_working=working,
        speed_kbps=speed,
    )


class TestFilterAndRank:
    def test_filters_out_non_working_results(self):
        results = [_make_result('fast.host', 500), _make_result('dead.host', 0, working=False)]
        ranked = filter_and_rank(results)
        assert len(ranked) == 1 and ranked[0].config.host == 'fast.host'

    def test_sorts_by_speed_descending(self):
        results = [_make_result('slow', 100), _make_result('fast', 900), _make_result('mid', 500)]
        ranked = filter_and_rank(results)
        assert [r.config.host for r in ranked] == ['fast', 'mid', 'slow']

    def test_returns_empty_list_for_all_failed(self):
        assert filter_and_rank([_make_result('a', 0, False), _make_result('b', 0, False)]) == []

    def test_returns_empty_list_for_empty_input(self):
        assert filter_and_rank([]) == []

    def test_single_working_result_returned_unchanged(self):
        ranked = filter_and_rank([_make_result('only', 300)])
        assert len(ranked) == 1 and ranked[0].config.host == 'only'

    def test_equal_speeds_are_all_included(self):
        assert len(filter_and_rank([_make_result('a', 200), _make_result('b', 200)])) == 2

    def test_does_not_mutate_original_list(self):
        results = [_make_result('slow', 50), _make_result('fast', 500)]
        original = [r.config.host for r in results]
        filter_and_rank(results)
        assert [r.config.host for r in results] == original
