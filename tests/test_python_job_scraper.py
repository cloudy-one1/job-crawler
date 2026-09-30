"""
python_job_scraper.build_api_params 函数的单元测试。
"""
import time
import pytest
from data.python_job_scraper import build_api_params


class TestBuildApiParams:
    """验证 build_api_params 返回的参数字典结构与关键行为。"""

    def test_basic_return_type(self):
        """返回类型必须是 dict"""
        result = build_api_params('python', '010000', 1)
        assert isinstance(result, dict)

    def test_all_keys_present(self):
        """测试返回字典包含所有必需的键"""
        expected_keys = {
            'api_key', 'timestamp', 'keyword', 'searchType', 'jobArea',
            'issueDate', 'sortType', 'pageNum', 'keywordType', 'pageSize',
            'source', 'pageCode', 'scene'
        }
        result = build_api_params('python', '010000', 1)
        assert set(result.keys()) == expected_keys

    def test_fixed_values_constant(self):
        """测试固定参数值是否正确"""
        result = build_api_params('python', '010000', 1)
        assert result['api_key'] == '51job'
        assert result['searchType'] == '2'
        assert result['issueDate'] == '4'
        assert result['sortType'] == '0'
        assert result['keywordType'] == '2'
        assert result['pageSize'] == '20'
        assert result['source'] == '1'
        assert result['pageCode'] == 'sou|sou|soulb'
        assert result['scene'] == '7'

    def test_keyword_parameter_passed(self):
        """测试 keyword 参数正确传递"""
        result = build_api_params('java', '010000', 1)
        assert result['keyword'] == 'java'

    def test_keyword_empty_string(self):
        """测试 keyword 为空字符串"""
        result = build_api_params('', '010000', 1)
        assert result['keyword'] == ''

    def test_keyword_chinese(self):
        """测试 keyword 包含中文字符"""
        result = build_api_params('数据分析师', '010000', 1)
        assert result['keyword'] == '数据分析师'

    def test_keyword_unicode(self):
        """测试 keyword 包含 Unicode 特殊字符"""
        result = build_api_params('C++', '010000', 1)
        assert result['keyword'] == 'C++'

    def test_keyword_special_chars(self):
        """测试 keyword 包含特殊字符"""
        result = build_api_params('python django', '010000', 1)
        assert result['keyword'] == 'python django'

    def test_keyword_with_spaces(self):
        """测试 keyword 包含多余空格"""
        result = build_api_params('  python  ', '010000', 1)
        assert result['keyword'] == '  python  '

    def test_job_area_parameter_passed(self):
        """测试 jobArea 参数正确传递"""
        result = build_api_params('python', '020000', 1)
        assert result['jobArea'] == '020000'

    def test_job_area_empty_string(self):
        """测试 jobArea 为空字符串"""
        result = build_api_params('python', '', 1)
        assert result['jobArea'] == ''

    def test_job_area_national(self):
        """测试 jobArea 为全国代码"""
        result = build_api_params('python', '000000', 1)
        assert result['jobArea'] == '000000'

    def test_page_num_parameter_passed(self):
        """测试 pageNum 参数正确传递"""
        result = build_api_params('python', '010000', 5)
        assert result['pageNum'] == 5

    def test_page_num_type_int(self):
        """测试 pageNum 类型为整数"""
        result = build_api_params('python', '010000', 1)
        assert isinstance(result['pageNum'], int)

    def test_page_num_one(self):
        """测试 pageNum 为最小值1"""
        result = build_api_params('python', '010000', 1)
        assert result['pageNum'] == 1

    def test_page_num_zero(self):
        """测试 pageNum 为零"""
        result = build_api_params('python', '010000', 0)
        assert result['pageNum'] == 0

    def test_page_num_large_value(self):
        """测试 pageNum 为较大值"""
        result = build_api_params('python', '010000', 100)
        assert result['pageNum'] == 100

    def test_page_num_negative(self):
        """测试 pageNum 为负数"""
        result = build_api_params('python', '010000', -1)
        assert result['pageNum'] == -1

    def test_timestamp_is_recent(self):
        """测试 timestamp 为当前时间戳(毫秒级)"""
        before = int(time.time() * 1000)
        result = build_api_params('python', '010000', 1)
        after = int(time.time() * 1000)
        assert before <= result['timestamp'] <= after

    def test_timestamp_monotonically_increasing(self):
        """测试多次调用 timestamp 递增(睡眠后)"""
        result1 = build_api_params('python', '010000', 1)
        time.sleep(0.01)
        result2 = build_api_params('python', '010000', 1)
        assert result2['timestamp'] > result1['timestamp']

    def test_combined_normal_input(self):
        """测试常规输入组合"""
        result = build_api_params('机器学习', '030200', 10)
        assert result['keyword'] == '机器学习'
        assert result['jobArea'] == '030200'
        assert result['pageNum'] == 10
        assert result['api_key'] == '51job'
        assert result['pageSize'] == '20'

    def test_combined_all_params_varied(self):
        """测试所有参数不同值的组合"""
        result = build_api_params('Go', '080200', 99)
        assert result['keyword'] == 'Go'
        assert result['jobArea'] == '080200'
        assert result['pageNum'] == 99

    def test_dict_immutable_fixed_fields(self):
        """测试固定字段值不依赖于输入参数"""
        result1 = build_api_params('a', 'x', 1)
        result2 = build_api_params('b', 'y', 2)
        assert result1['api_key'] == result2['api_key'] == '51job'
        assert result1['searchType'] == result2['searchType'] == '2'
        assert result1['issueDate'] == result2['issueDate'] == '4'
        assert result1['sortType'] == result2['sortType'] == '0'
        assert result1['keywordType'] == result2['keywordType'] == '2'
        assert result1['pageSize'] == result2['pageSize'] == '20'
        assert result1['source'] == result2['source'] == '1'
        assert result1['pageCode'] == result2['pageCode'] == 'sou|sou|soulb'
        assert result1['scene'] == result2['scene'] == '7'

    def test_dict_length(self):
        """测试返回字典长度为13"""
        result = build_api_params('python', '010000', 1)
        assert len(result) == 13

    def test_no_extra_keys(self):
        """测试返回字典没有额外的键"""
        result = build_api_params('python', '010000', 1)
        extra_keys = set(result.keys()) - {
            'api_key', 'timestamp', 'keyword', 'searchType', 'jobArea',
            'issueDate', 'sortType', 'pageNum', 'keywordType', 'pageSize',
            'source', 'pageCode', 'scene'
        }
        assert len(extra_keys) == 0

    def test_timestamp_granularity_milliseconds(self):
        """测试 timestamp 是毫秒级精度"""
        result = build_api_params('python', '010000', 1)
        timestamp = result['timestamp']
        assert timestamp > 1_000_000_000_000

    def test_multiple_calls_independent(self):
        """测试多次调用返回独立的字典对象"""
        result1 = build_api_params('python', '010000', 1)
        result2 = build_api_params('python', '010000', 1)
        assert result1 is not result2


# ============================================================
# _build_address — 库内 address 口径
# ============================================================
class TestBuildAddress:
    """验证 jobAreaString → address 的规范化。

    重点回归：全国搜索（city='全国'）时 jobAreaString 自带真实城市，
    旧版会拼出「全国-上海-浦东新区」，污染城市分布/收藏/匹配的城市字段。
    """

    def test_city_prefixed_area_kept_as_is(self):
        from data.python_job_scraper import _build_address
        assert _build_address('北京', '北京-海淀区') == '北京-海淀区'

    def test_city_prepended_when_area_differs(self):
        from data.python_job_scraper import _build_address
        assert _build_address('北京', '朝阳区') == '北京-朝阳区'

    def test_nationwide_uses_area_city(self):
        """全国搜索不得拼「全国-」前缀。"""
        from data.python_job_scraper import _build_address
        assert _build_address('全国', '上海-浦东新区') == '上海-浦东新区'

    def test_nationwide_empty_area_falls_back(self):
        from data.python_job_scraper import _build_address
        assert _build_address('全国', '') == '全国'

    def test_empty_area_returns_city_only(self):
        from data.python_job_scraper import _build_address
        assert _build_address('杭州', '') == '杭州'

    def test_middle_dot_converted_to_dash(self):
        from data.python_job_scraper import _build_address
        assert _build_address('苏州', '苏州·工业园区') == '苏州-工业园区'
