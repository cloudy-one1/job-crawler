"""
51job 实时采集模块(可参数化版本)。

跟最早那版的区别: 之前关键词("python")和城市(北京/上海/广州/深圳)是写死的常量,
这一版改成函数参数,Flask那边可以接收用户在网页上填的关键词+城市,直接调用
scrape_jobs() 拿到结果,不需要再改代码。

技术思路参考: https://github.com/gitychzh/jobSpider (无LICENSE声明,
本文件未直接复制该仓库代码,而是参考其"Playwright过WAF + 浏览器内fetch调用
真实API"的思路自行重写)。

依赖安装: pip install playwright playwright-stealth
         playwright install chromium

用法(命令行直接跑,效果跟网页上的"实时采集"完全一致——直接写入data.db):
    python data/python_job_scraper.py

用法(被其他代码调用,比如Flask路由):
    from data.python_job_scraper import scrape_jobs
    jobs = scrape_jobs(keyword='java', cities=['杭州', '成都'], pages_per_city=2)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import random
import logging
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_logger = logging.getLogger('job_analysis')

# 51job城市代码表(来自51job官方CDN: js.51jobcdn.com/in/js/2016/layer/area_array_c.js,
# 2024-03-29更新, 覆盖全国388个地级市)
CITY_CODES = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "惠州": "030300",
    "汕头": "030400",
    "珠海": "030500",
    "佛山": "030600",
    "中山": "030700",
    "东莞": "030800",
    "韶关": "031400",
    "江门": "031500",
    "湛江": "031700",
    "肇庆": "031800",
    "清远": "031900",
    "潮州": "032000",
    "河源": "032100",
    "揭阳": "032200",
    "茂名": "032300",
    "汕尾": "032400",
    "梅州": "032600",
    "开平": "032700",
    "阳江": "032800",
    "云浮": "032900",
    "深圳": "040000",
    "天津": "050000",
    "重庆": "060000",
    "南京": "070200",
    "苏州": "070300",
    "无锡": "070400",
    "常州": "070500",
    "昆山": "070600",
    "常熟": "070700",
    "扬州": "070800",
    "南通": "070900",
    "镇江": "071000",
    "徐州": "071100",
    "连云港": "071200",
    "盐城": "071300",
    "张家港": "071400",
    "太仓": "071600",
    "泰州": "071800",
    "淮安": "071900",
    "宿迁": "072000",
    "杭州": "080200",
    "宁波": "080300",
    "温州": "080400",
    "绍兴": "080500",
    "金华": "080600",
    "嘉兴": "080700",
    "台州": "080800",
    "湖州": "080900",
    "丽水": "081000",
    "舟山": "081100",
    "衢州": "081200",
    "义乌": "081400",
    "海宁": "081600",
    "成都": "090200",
    "绵阳": "090300",
    "乐山": "090400",
    "泸州": "090500",
    "德阳": "090600",
    "宜宾": "090700",
    "自贡": "090800",
    "内江": "090900",
    "攀枝花": "091000",
    "南充": "091100",
    "眉山": "091200",
    "广安": "091300",
    "资阳": "091400",
    "遂宁": "091500",
    "广元": "091600",
    "达州": "091700",
    "雅安": "091800",
    "西昌": "091900",
    "巴中": "092000",
    "甘孜": "092100",
    "阿坝": "092200",
    "凉山": "092300",
    "海口": "100200",
    "三亚": "100300",
    "洋浦经济开发区": "100400",
    "文昌": "100500",
    "琼海": "100600",
    "万宁": "100700",
    "儋州": "100800",
    "东方": "100900",
    "五指山": "101000",
    "定安": "101100",
    "屯昌": "101200",
    "澄迈": "101300",
    "临高": "101400",
    "三沙": "101500",
    "琼中": "101600",
    "保亭": "101700",
    "白沙": "101800",
    "昌江": "101900",
    "乐东": "102000",
    "陵水": "102100",
    "福州": "110200",
    "厦门": "110300",
    "泉州": "110400",
    "漳州": "110500",
    "莆田": "110600",
    "三明": "110700",
    "南平": "110800",
    "宁德": "110900",
    "龙岩": "111000",
    "济南": "120200",
    "青岛": "120300",
    "烟台": "120400",
    "潍坊": "120500",
    "威海": "120600",
    "淄博": "120700",
    "临沂": "120800",
    "济宁": "120900",
    "东营": "121000",
    "泰安": "121100",
    "日照": "121200",
    "德州": "121300",
    "菏泽": "121400",
    "滨州": "121500",
    "枣庄": "121600",
    "聊城": "121700",
    "南昌": "130200",
    "九江": "130300",
    "景德镇": "130400",
    "萍乡": "130500",
    "新余": "130600",
    "鹰潭": "130700",
    "赣州": "130800",
    "吉安": "130900",
    "宜春": "131000",
    "抚州": "131100",
    "上饶": "131200",
    "南宁": "140200",
    "桂林": "140300",
    "柳州": "140400",
    "北海": "140500",
    "玉林": "140600",
    "梧州": "140700",
    "防城港": "140800",
    "钦州": "140900",
    "贵港": "141000",
    "百色": "141100",
    "河池": "141200",
    "来宾": "141300",
    "崇左": "141400",
    "贺州": "141500",
    "合肥": "150200",
    "芜湖": "150300",
    "安庆": "150400",
    "马鞍山": "150500",
    "蚌埠": "150600",
    "阜阳": "150700",
    "铜陵": "150800",
    "滁州": "150900",
    "黄山": "151000",
    "淮南": "151100",
    "六安": "151200",
    "宣城": "151400",
    "池州": "151500",
    "宿州": "151600",
    "淮北": "151700",
    "亳州": "151800",
    "雄安新区": "160100",
    "石家庄": "160200",
    "廊坊": "160300",
    "保定": "160400",
    "唐山": "160500",
    "秦皇岛": "160600",
    "邯郸": "160700",
    "沧州": "160800",
    "张家口": "160900",
    "承德": "161000",
    "邢台": "161100",
    "衡水": "161200",
    "燕郊开发区": "161300",
    "郑州": "170200",
    "洛阳": "170300",
    "开封": "170400",
    "焦作": "170500",
    "南阳": "170600",
    "新乡": "170700",
    "周口": "170800",
    "安阳": "170900",
    "平顶山": "171000",
    "许昌": "171100",
    "信阳": "171200",
    "商丘": "171300",
    "驻马店": "171400",
    "漯河": "171500",
    "濮阳": "171600",
    "鹤壁": "171700",
    "三门峡": "171800",
    "济源": "171900",
    "邓州": "172000",
    "武汉": "180200",
    "宜昌": "180300",
    "黄石": "180400",
    "襄阳": "180500",
    "十堰": "180600",
    "荆州": "180700",
    "荆门": "180800",
    "孝感": "180900",
    "鄂州": "181000",
    "黄冈": "181100",
    "随州": "181200",
    "咸宁": "181300",
    "仙桃": "181400",
    "潜江": "181500",
    "天门": "181600",
    "神农架": "181700",
    "恩施": "181800",
    "长沙": "190200",
    "株洲": "190300",
    "湘潭": "190400",
    "衡阳": "190500",
    "岳阳": "190600",
    "常德": "190700",
    "益阳": "190800",
    "郴州": "190900",
    "邵阳": "191000",
    "怀化": "191100",
    "娄底": "191200",
    "永州": "191300",
    "张家界": "191400",
    "湘西": "191500",
    "西安": "200200",
    "咸阳": "200300",
    "宝鸡": "200400",
    "铜川": "200500",
    "延安": "200600",
    "渭南": "200700",
    "榆林": "200800",
    "汉中": "200900",
    "安康": "201000",
    "商洛": "201100",
    "杨凌": "201200",
    "太原": "210200",
    "运城": "210300",
    "大同": "210400",
    "临汾": "210500",
    "长治": "210600",
    "晋城": "210700",
    "阳泉": "210800",
    "朔州": "210900",
    "晋中": "211000",
    "忻州": "211100",
    "吕梁": "211200",
    "哈尔滨": "220200",
    "伊春": "220300",
    "绥化": "220400",
    "大庆": "220500",
    "齐齐哈尔": "220600",
    "牡丹江": "220700",
    "佳木斯": "220800",
    "鸡西": "220900",
    "鹤岗": "221000",
    "双鸭山": "221100",
    "黑河": "221200",
    "七台河": "221300",
    "大兴安岭": "221400",
    "沈阳": "230200",
    "大连": "230300",
    "鞍山": "230400",
    "营口": "230500",
    "抚顺": "230600",
    "锦州": "230700",
    "丹东": "230800",
    "葫芦岛": "230900",
    "本溪": "231000",
    "辽阳": "231100",
    "铁岭": "231200",
    "盘锦": "231300",
    "朝阳": "231400",
    "阜新": "231500",
    "长春": "240200",
    "吉林": "240300",
    "辽源": "240400",
    "通化": "240500",
    "四平": "240600",
    "松原": "240700",
    "延吉": "240800",
    "白山": "240900",
    "白城": "241000",
    "延边": "241100",
    "昆明": "250200",
    "曲靖": "250300",
    "玉溪": "250400",
    "大理": "250500",
    "丽江": "250600",
    "红河州": "251000",
    "普洱": "251100",
    "保山": "251200",
    "昭通": "251300",
    "文山": "251400",
    "西双版纳": "251500",
    "德宏": "251600",
    "楚雄": "251700",
    "临沧": "251800",
    "怒江": "251900",
    "迪庆": "252000",
    "贵阳": "260200",
    "遵义": "260300",
    "六盘水": "260400",
    "安顺": "260500",
    "铜仁": "260600",
    "毕节": "260700",
    "黔西南": "260800",
    "黔东南": "260900",
    "黔南": "261000",
    "兰州": "270200",
    "金昌": "270300",
    "嘉峪关": "270400",
    "酒泉": "270500",
    "天水": "270600",
    "武威": "270700",
    "白银": "270800",
    "张掖": "270900",
    "平凉": "271000",
    "定西": "271100",
    "陇南": "271200",
    "庆阳": "271300",
    "临夏": "271400",
    "甘南": "271500",
    "呼和浩特": "280200",
    "赤峰": "280300",
    "包头": "280400",
    "通辽": "280700",
    "鄂尔多斯": "280800",
    "巴彦淖尔": "280900",
    "乌海": "281000",
    "呼伦贝尔": "281100",
    "乌兰察布": "281200",
    "兴安盟": "281300",
    "锡林郭勒盟": "281400",
    "阿拉善盟": "281500",
    "银川": "290200",
    "吴忠": "290300",
    "中卫": "290400",
    "石嘴山": "290500",
    "固原": "290600",
    "拉萨": "300200",
    "日喀则": "300300",
    "林芝": "300400",
    "山南": "300500",
    "昌都": "300600",
    "那曲": "300700",
    "阿里": "300800",
    "乌鲁木齐": "310200",
    "克拉玛依": "310300",
    "喀什地区": "310400",
    "伊犁": "310500",
    "阿克苏": "310600",
    "哈密": "310700",
    "石河子": "310800",
    "阿拉尔": "310900",
    "五家渠": "311000",
    "图木舒克": "311100",
    "昌吉": "311200",
    "阿勒泰": "311300",
    "吐鲁番": "311400",
    "塔城": "311500",
    "和田": "311600",
    "克孜勒苏柯尔克孜": "311700",
    "巴音郭楞": "311800",
    "博尔塔拉": "311900",
    "昆玉": "312000",
    "北屯": "312100",
    "铁门关": "312200",
    "可克达拉": "312300",
    "胡杨河": "312400",
    "双河": "312500",
    "新星": "312600",
    "西宁": "320200",
    "海东": "320300",
    "海西": "320400",
    "海北": "320500",
    "黄南": "320600",
    "海南州": "320700",
    "果洛": "320800",
    "玉树": "320900",
    "亚洲": "361000",
    "欧洲": "362000",
    "美洲": "363000",
    "非洲": "364000",
    "大洋洲": "365000",
    "其他": "366000",
}

# 省份代码前缀 → 省份名称（用于前端省份-城市级联选择）
PROVINCE_MAP = {
    '01': '北京', '02': '上海', '03': '广东', '04': '广东',
    '05': '天津', '06': '重庆', '07': '江苏', '08': '浙江',
    '09': '四川', '10': '海南', '11': '福建', '12': '山东',
    '13': '江西', '14': '广西', '15': '安徽', '16': '河北',
    '17': '河南', '18': '湖北', '19': '湖南', '20': '陕西',
    '21': '山西', '22': '黑龙江', '23': '辽宁', '24': '吉林',
    '25': '云南', '26': '贵州', '27': '甘肃', '28': '内蒙古',
    '29': '宁夏', '30': '西藏', '31': '新疆', '32': '青海',
    '36': '国外',
}


def get_province_city_map():
    """返回 {省份名: [(城市名, 城市代码), ...]} 的映射,用于前端级联选择"""
    grouped = {}
    for city, code in CITY_CODES.items():
        prefix = code[:2]
        province = PROVINCE_MAP.get(prefix, f'其他({prefix})')
        grouped.setdefault(province, []).append((city, code))
    return grouped

# 中文城市名 → 拼音映射(用于构建 51job 原始链接 URL, 硬编码字典)
CITY_PINYIN = {
    "七台河": "qitaihe",
    "万宁": "wanning",
    "三亚": "sanya",
    "三明": "sanming",
    "三沙": "sansha",
    "三门峡": "sanmenxia",
    "上海": "shanghai",
    "上饶": "shangrao",
    "东方": "dongfang",
    "东莞": "dongguan",
    "东营": "dongying",
    "中卫": "zhongwei",
    "中山": "zhongshan",
    "临夏": "linxia",
    "临汾": "linfen",
    "临沂": "linyi",
    "临沧": "lincang",
    "临高": "lingao",
    "丹东": "dandong",
    "丽水": "lishui",
    "丽江": "lijiang",
    "义乌": "yiwu",
    "乌兰察布": "wulanchabu",
    "乌海": "wuhai",
    "乌鲁木齐": "wulumuqi",
    "乐东": "ledong",
    "乐山": "leshan",
    "九江": "jiujiang",
    "云浮": "yunfu",
    "五家渠": "wujiaqu",
    "五指山": "wuzhishan",
    "亚洲": "yazhou",
    "亳州": "bozhou",
    "仙桃": "xiantao",
    "伊春": "yichun",
    "伊犁": "yili",
    "佛山": "foshan",
    "佳木斯": "jiamusi",
    "保亭": "baoting",
    "保定": "baoding",
    "保山": "baoshan",
    "信阳": "xinyang",
    "儋州": "danzhou",
    "克孜勒苏柯尔克孜": "kezileisukeerkezi",
    "克拉玛依": "kelamayi",
    "六安": "luan",
    "六盘水": "liupanshui",
    "兰州": "lanzhou",
    "兴安盟": "xinganmeng",
    "其他": "qita",
    "内江": "neijiang",
    "凉山": "liangshan",
    "包头": "baotou",
    "北京": "beijing",
    "北屯": "beitun",
    "北海": "beihai",
    "十堰": "shiyan",
    "南京": "nanjing",
    "南充": "nanchong",
    "南宁": "nanning",
    "南平": "nanping",
    "南昌": "nanchang",
    "南通": "nantong",
    "南阳": "nanyang",
    "博尔塔拉": "boertala",
    "厦门": "xiamen",
    "双河": "shuanghe",
    "双鸭山": "shuangyashan",
    "可克达拉": "kekedala",
    "台州": "taizhou",
    "合肥": "hefei",
    "吉安": "jian",
    "吉林": "jilin",
    "吐鲁番": "tulufan",
    "吕梁": "lvliang",
    "吴忠": "wuzhong",
    "周口": "zhoukou",
    "呼伦贝尔": "hulunbeier",
    "呼和浩特": "huhehaote",
    "和田": "hetian",
    "咸宁": "xianning",
    "咸阳": "xianyang",
    "哈密": "hami",
    "哈尔滨": "haerbin",
    "唐山": "tangshan",
    "商丘": "shangqiu",
    "商洛": "shangluo",
    "喀什地区": "kashidiqu",
    "嘉兴": "jiaxing",
    "嘉峪关": "jiayuguan",
    "四平": "siping",
    "固原": "guyuan",
    "图木舒克": "tumushuke",
    "塔城": "tacheng",
    "大兴安岭": "daxinganling",
    "大同": "datong",
    "大庆": "daqing",
    "大洋洲": "dayangzhou",
    "大理": "dali",
    "大连": "dalian",
    "天水": "tianshui",
    "天津": "tianjin",
    "天门": "tianmen",
    "太仓": "taicang",
    "太原": "taiyuan",
    "威海": "weihai",
    "娄底": "loudi",
    "孝感": "xiaogan",
    "宁德": "ningde",
    "宁波": "ningbo",
    "安庆": "anqing",
    "安康": "ankang",
    "安阳": "anyang",
    "安顺": "anshun",
    "定安": "dingan",
    "定西": "dingxi",
    "宜宾": "yibin",
    "宜昌": "yichang",
    "宜春": "yichun",
    "宝鸡": "baoji",
    "宣城": "xuancheng",
    "宿州": "suzhou",
    "宿迁": "suqian",
    "屯昌": "tunchang",
    "山南": "shannan",
    "岳阳": "yueyang",
    "崇左": "chongzuo",
    "巴中": "bazhong",
    "巴彦淖尔": "bayannaoer",
    "巴音郭楞": "bayinguoleng",
    "常州": "changzhou",
    "常德": "changde",
    "常熟": "changshu",
    "平凉": "pingliang",
    "平顶山": "pingdingshan",
    "广元": "guangyuan",
    "广安": "guangan",
    "广州": "guangzhou",
    "庆阳": "qingyang",
    "廊坊": "langfang",
    "延吉": "yanji",
    "延安": "yanan",
    "延边": "yanbian",
    "开封": "kaifeng",
    "开平": "kaiping",
    "张家口": "zhangjiakou",
    "张家港": "zhangjiagang",
    "张家界": "zhangjiajie",
    "张掖": "zhangye",
    "徐州": "xuzhou",
    "德宏": "dehong",
    "德州": "dezhou",
    "德阳": "deyang",
    "忻州": "xinzhou",
    "怀化": "huaihua",
    "怒江": "nujiang",
    "恩施": "enshi",
    "惠州": "huizhou",
    "成都": "chengdu",
    "扬州": "yangzhou",
    "承德": "chengde",
    "抚州": "fuzhou",
    "抚顺": "fushun",
    "拉萨": "lasa",
    "揭阳": "jieyang",
    "攀枝花": "panzhihua",
    "文山": "wenshan",
    "文昌": "wenchang",
    "新乡": "xinxiang",
    "新余": "xinyu",
    "新星": "xinxing",
    "无锡": "wuxi",
    "日喀则": "rikaze",
    "日照": "rizhao",
    "昆山": "kunshan",
    "昆明": "kunming",
    "昆玉": "kunyu",
    "昌吉": "changji",
    "昌江": "changjiang",
    "昌都": "changdou",
    "昭通": "zhaotong",
    "晋中": "jinzhong",
    "晋城": "jincheng",
    "普洱": "puer",
    "景德镇": "jingdezhen",
    "曲靖": "qujing",
    "朔州": "shuozhou",
    "朝阳": "zhaoyang",
    "本溪": "benxi",
    "来宾": "laibin",
    "杨凌": "yangling",
    "杭州": "hangzhou",
    "松原": "songyuan",
    "林芝": "linzhi",
    "果洛": "guoluo",
    "枣庄": "zaozhuang",
    "柳州": "liuzhou",
    "株洲": "zhuzhou",
    "桂林": "guilin",
    "梅州": "meizhou",
    "梧州": "wuzhou",
    "楚雄": "chuxiong",
    "榆林": "yulin",
    "欧洲": "ouzhou",
    "武威": "wuwei",
    "武汉": "wuhan",
    "毕节": "bijie",
    "永州": "yongzhou",
    "汉中": "hanzhong",
    "汕头": "shantou",
    "汕尾": "shanwei",
    "江门": "jiangmen",
    "池州": "chizhou",
    "沈阳": "shenyang",
    "沧州": "cangzhou",
    "河池": "hechi",
    "河源": "heyuan",
    "泉州": "quanzhou",
    "泰安": "taian",
    "泰州": "taizhou",
    "泸州": "luzhou",
    "洋浦经济开发区": "yangpujingjikaifaqu",
    "洛阳": "luoyang",
    "济南": "jinan",
    "济宁": "jining",
    "济源": "jiyuan",
    "海东": "haidong",
    "海北": "haibei",
    "海南州": "hainanzhou",
    "海口": "haikou",
    "海宁": "haining",
    "海西": "haixi",
    "淄博": "zibo",
    "淮北": "huaibei",
    "淮南": "huainan",
    "淮安": "huaian",
    "深圳": "shenzhen",
    "清远": "qingyuan",
    "温州": "wenzhou",
    "渭南": "weinan",
    "湖州": "huzhou",
    "湘潭": "xiangtan",
    "湘西": "xiangxi",
    "湛江": "zhanjiang",
    "滁州": "chuzhou",
    "滨州": "binzhou",
    "漯河": "tahe",
    "漳州": "zhangzhou",
    "潍坊": "weifang",
    "潜江": "qianjiang",
    "潮州": "chaozhou",
    "澄迈": "chengmai",
    "濮阳": "puyang",
    "烟台": "yantai",
    "焦作": "jiaozuo",
    "燕郊开发区": "yanjiaokaifaqu",
    "牡丹江": "mudanjiang",
    "玉林": "yulin",
    "玉树": "yushu",
    "玉溪": "yuxi",
    "珠海": "zhuhai",
    "琼中": "qiongzhong",
    "琼海": "qionghai",
    "甘南": "gannan",
    "甘孜": "ganzi",
    "白城": "baicheng",
    "白山": "baishan",
    "白沙": "baisha",
    "白银": "baiyin",
    "百色": "baise",
    "益阳": "yiyang",
    "盐城": "yancheng",
    "盘锦": "panjin",
    "眉山": "meishan",
    "石嘴山": "shizuishan",
    "石家庄": "shijiazhuang",
    "石河子": "shihezi",
    "神农架": "shennongjia",
    "福州": "fuzhou",
    "秦皇岛": "qinhuangdao",
    "红河州": "honghezhou",
    "绍兴": "shaoxing",
    "绥化": "suihua",
    "绵阳": "mianyang",
    "美洲": "meizhou",
    "聊城": "liaocheng",
    "肇庆": "zhaoqing",
    "胡杨河": "huyanghe",
    "自贡": "zigong",
    "舟山": "zhoushan",
    "芜湖": "wuhu",
    "苏州": "suzhou",
    "茂名": "maoming",
    "荆州": "jingzhou",
    "荆门": "jingmen",
    "莆田": "putian",
    "菏泽": "heze",
    "萍乡": "pingxiang",
    "营口": "yingkou",
    "葫芦岛": "huludao",
    "蚌埠": "bengbu",
    "衡水": "hengshui",
    "衡阳": "hengyang",
    "衢州": "quzhou",
    "襄阳": "xiangyang",
    "西双版纳": "xishuangbanna",
    "西宁": "xining",
    "西安": "xian",
    "西昌": "xichang",
    "许昌": "xuchang",
    "贵港": "guigang",
    "贵阳": "guiyang",
    "贺州": "hezhou",
    "资阳": "ziyang",
    "赣州": "ganzhou",
    "赤峰": "chifeng",
    "辽源": "liaoyuan",
    "辽阳": "liaoyang",
    "达州": "dazhou",
    "运城": "yuncheng",
    "连云港": "lianyungang",
    "迪庆": "diqing",
    "通化": "tonghua",
    "通辽": "tongliao",
    "遂宁": "suining",
    "遵义": "zunyi",
    "邓州": "dengzhou",
    "邢台": "xingtai",
    "那曲": "naqu",
    "邯郸": "handan",
    "邵阳": "shaoyang",
    "郑州": "zhengzhou",
    "郴州": "chenzhou",
    "鄂尔多斯": "eerduosi",
    "鄂州": "ezhou",
    "酒泉": "jiuquan",
    "重庆": "chongqing",
    "金华": "jinhua",
    "金昌": "jinchang",
    "钦州": "qinzhou",
    "铁岭": "tieling",
    "铁门关": "tiemenguan",
    "铜仁": "tongren",
    "铜川": "tongchuan",
    "铜陵": "tongling",
    "银川": "yinchuan",
    "锡林郭勒盟": "xilinguoleimeng",
    "锦州": "jinzhou",
    "镇江": "zhenjiang",
    "长春": "changchun",
    "长沙": "changsha",
    "长治": "zhangzhi",
    "阜新": "fuxin",
    "阜阳": "fuyang",
    "防城港": "fangchenggang",
    "阳江": "yangjiang",
    "阳泉": "yangquan",
    "阿克苏": "akesu",
    "阿勒泰": "aleitai",
    "阿坝": "aba",
    "阿拉善盟": "alashanmeng",
    "阿拉尔": "alaer",
    "阿里": "ali",
    "陇南": "longnan",
    "陵水": "lingshui",
    "随州": "suizhou",
    "雄安新区": "xionganxinqu",
    "雅安": "yaan",
    "青岛": "qingdao",
    "非洲": "feizhou",
    "鞍山": "anshan",
    "韶关": "shaoguan",
    "马鞍山": "maanshan",
    "驻马店": "zhumadian",
    "鸡西": "jixi",
    "鹤壁": "hebi",
    "鹤岗": "hegang",
    "鹰潭": "yingtan",
    "黄冈": "huanggang",
    "黄南": "huangnan",
    "黄山": "huangshan",
    "黄石": "huangshi",
    "黑河": "heihe",
    "黔东南": "qiandongnan",
    "黔南": "qiannan",
    "黔西南": "qianxinan",
    "齐齐哈尔": "qiqihaer",
    "龙岩": "longyan",
}



JS_FETCH_API = """
async (params) => {
    const url = 'https://we.51job.com/api/job/search-pc?' + new URLSearchParams(params).toString();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);  // 15秒超时
    try {
        const res = await fetch(url, {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Referer': window.location.href,
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
            },
            signal: controller.signal
        });
        clearTimeout(timeout);
        if (!res.ok) return {error: 'HTTP ' + res.status};
        const text = await res.text();
        if (text.startsWith('<') || text.length < 100) return {error: 'WAF拦截/返回HTML'};
        return JSON.parse(text);
    } catch(e) {
        clearTimeout(timeout);
        return {error: e.name === 'AbortError' ? '请求超时(20s)' : e.message};
    }
}
"""


def resolve_city_code(city_name):
    """把用户输入的城市名转成51job城市代码,找不到返回None"""
    city_name = city_name.strip()
    if city_name in CITY_CODES:
        return CITY_CODES[city_name]
    # 容错: 用户输入"广东"这种省份名,或者打错字带了"市"字,做一个宽松匹配。
    # 但只在输入长度合理(<=6个字符)时才做这个模糊匹配——
    # 真实城市/省份名不会很长,如果传进来的是一长串没拆开的文字
    # (比如逗号分隔符没识别导致多个城市名粘在一起),不应该被误判匹配上
    # 某个城市(这是实测踩到过的真实bug,城市拆分失败时曾经误判成功过)。
    if len(city_name) <= 6:
        for name, code in CITY_CODES.items():
            if name in city_name or city_name in name:
                return code
    return None


def build_api_params(keyword, job_area, page_num, sort_type='0'):
    return {
        'api_key': '51job',
        'timestamp': int(time.time() * 1000),
        'keyword': keyword,
        'searchType': '2',
        'jobArea': job_area,
        'issueDate': '4',
        'sortType': sort_type,
        'pageNum': page_num,
        'keywordType': '2',
        'pageSize': '20',
        'source': '1',
        'pageCode': 'sou|sou|soulb',
        'scene': '7',
    }


def _evaluate_with_timeout(page, js_func, params, timeout_ms=28000):
    """
    调用 page.evaluate() 并带上显式超时(默认28s), 处理超时与异常。

    返回值:
        (result_data, page_dead: bool)
        page_dead=True 表示页面可能已失效, 调用方应重建 page。

    说明:
        page.set_default_timeout() 控制的是 Playwright 内部事件循环的等待上限,
        但在浏览器进程半僵死时(time_wait状态/WebSocket半开), 内部超时可能也失效。
        此时本函数会阻塞至多 timeout_ms 毫秒后放弃, 并告知调用方重建页面。
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError
    try:
        result = page.evaluate(js_func, params)
        return result, False
    except PWTimeoutError:
        _logger.warning('page.evaluate() 超时(%dms), 页面可能已僵死', timeout_ms)
        return {'error': f'evaluate超时({timeout_ms}ms)'}, True
    except Exception as e:
        err_msg = str(e).lower()
        # 连接断开/页面关闭类错误也意味着页面死亡
        if any(kw in err_msg for kw in ('closed', 'target closed', 'been closed',
                                          'websocket', 'connection', 'disconnected')):
            _logger.warning('page.evaluate() 连接断开: %s', e)
            return {'error': f'连接断开: {e}'}, True
        raise  # 其他异常继续上抛


def scrape_jobs(keyword, cities, pages_per_city=3, sort_type='0', progress_callback=None, save_callback=None):
    """
    核心函数: 给定关键词 + 城市名列表,实时采集51job数据。

    参数:
        keyword: 搜索关键词,比如 'python' / 'java'
        cities: 城市名列表,比如 ['北京', '上海'];传空列表时默认全国范围搜索
        pages_per_city: 每个城市采集几页,每页20条
        sort_type: 排序方式, '0'=综合排序(默认), '1'=最新发布
        progress_callback: 可选,一个函数(city, page, count) -> None,
                用于在网页上实时显示采集进度(比如Flask里可以传一个打印日志的函数)
        save_callback: 可选,一个函数(city, jobs_for_city) -> None,
                每采集完一个城市后调用,用于增量写入DB(防Ctrl+C丢数据)

    返回: list of dict,字段跟项目数据库schema一致
          (post, company, address, salary_raw, edu, exper, dateT, scrape_date)

    注意: 这个函数会真的打开一个无头浏览器访问51job,耗时通常是
          "5~10秒过WAF" + "每页约0.3秒",城市越多、页数越多越慢。
          调用方(比如Flask路由)要注意这是同步阻塞调用,不要在每个普通请求里
          都触发,只应该作为一个用户主动点击的"实时采集"动作。
    """
    valid_cities = []
    for c in cities:
        code = resolve_city_code(c)
        if code:
            valid_cities.append((c, code))

    if not valid_cities:
        # 没有指定城市 → 全国范围搜索
        valid_cities = [("全国", "000000")]

    all_jobs = []
    all_seen = set()
    pages_collected = {}  # city → 实际翻到的页数

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--lang=zh-CN',
            ],
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            extra_http_headers={
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
            },
        )
        Stealth().apply_stealth_sync(context)

        # CDP 层面彻底覆盖 navigator.webdriver 等自动化指纹
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = {runtime: {}};
        """)

        page = context.new_page()
        # 设置页面默认超时30秒: evaluate/navigation等操作超过30s自动抛异常
        # 解决最后一页JS fetch挂死导致Python主线程无限阻塞的问题
        page.set_default_timeout(30000)

        # 第一步: 先访问 51job 首页预热, 拿到 WAF 放行的 cookie
        # 用 wait_until='commit' 只要导航发生即可, 不等整个 DOM 加载完(首页太重)
        _logger.info('预热中: 访问 51job 首页获取 cookie...')
        try:
            page.goto('https://we.51job.com/', timeout=20000, wait_until='commit')
            # 模拟人类浏览: 随机滚动 + 停留
            time.sleep(random.uniform(1.5, 2.5))
            try:
                page.evaluate("window.scrollTo(0, 300)")
            except Exception:
                pass
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            _logger.warning('首页预热超时/失败(%s), 直接尝试搜索页', e)

        # 第二步: 跳转到搜索页
        # 用 wait_until='commit' 只要导航发生即可——数据靠浏览器内 fetch API 获取,
        # 不依赖页面 DOM 渲染, 避免 51job 搜索页加载慢导致 domcontentloaded 超时
        first_code = valid_cities[0][1]
        search_url = (
            f"https://we.51job.com/pc/search?keyword={keyword}&keywordType=2"
            f"&jobArea={first_code}&issuedDate=4&pageNum=1&pageSize=20"
        )
        try:
            page.goto(search_url, timeout=60000, wait_until='commit')
            # 给页面时间让 WAF cookie 落地 + 部分加载
            time.sleep(random.uniform(2.0, 3.0))
        except Exception as e:
            _logger.warning('搜索页加载超时/失败(%s), 尝试继续 fetch...', e)

        # WAF验证等待: 前15轮每0.5s(7.5s), 后20轮每1s(20s), 最多等30s
        waf_passed = False
        for round_idx in range(35):
            try:
                # 多信号检测: joblist 出现即认为通过
                cnt = page.evaluate(
                    "document.querySelectorAll('.joblist-item, .j_joblist, .el').length"
                )
                if cnt >= 1:
                    waf_passed = True
                    elapsed = round_idx * 0.5 if round_idx < 15 else 7.5 + (round_idx - 15)
                    _logger.info('WAF验证通过 (耗时约 %.1f 秒)', elapsed)
                    break
            except Exception:
                pass
            # 模拟人类: 每隔几轮随机小幅滚动, 避免被判定为机器人
            if round_idx > 0 and round_idx % 5 == 0:
                try:
                    page.evaluate(f"window.scrollTo(0, {random.randint(100, 500)})")
                except Exception:
                    pass
            wait_s = 0.5 if round_idx < 15 else 1.0
            time.sleep(wait_s)
        else:
            _logger.warning('WAF验证超时(30s), 51job可能拦截了请求, 尝试继续...')

        if waf_passed:
            # 通过 WAF 后再模拟一次人类停留, 让会话更自然
            time.sleep(random.uniform(0.5, 1.0))

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        for city, code in valid_cities:
            _logger.info('开始采集: %s', city)
            pages_collected[city] = 0
            city_start_idx = len(all_jobs)  # 记录该城市采集前的数据量,用于增量保存
            _interrupted = False
            try:
                for pg in range(1, pages_per_city + 1):
                    # 单页硬超时: 每页(含重试)最多60秒, 超时跳过该城市后续页
                    page_start = time.time()
                    params = build_api_params(keyword, code, pg, sort_type)
                    # 前3页快速翻(模拟正常浏览),后面逐渐放慢避免触发风控
                    delay = random.uniform(0.1, 0.3) if pg <= 3 else random.uniform(0.3, 0.6)
                    time.sleep(delay)

                    # 单页重试机制: 最多尝试3次, 失败时模拟人类行为后重试
                    data = None
                    page_dead = False
                    for attempt in range(3):
                        # 检查是否超出单页总时限
                        if time.time() - page_start > 60:
                            _logger.warning('[%s] 第%d页超过60秒硬超时, 跳过该城市后续页', city, pg)
                            break
                        # 如果上一轮页面已失效, 先重建
                        if page_dead:
                            try:
                                page.close()
                            except Exception:
                                pass
                            page = context.new_page()
                            page.set_default_timeout(30000)
                            # 重建后需重新导航到搜索页, 否则 cookie/上下文丢失
                            try:
                                search_url = (
                                    f"https://we.51job.com/pc/search?keyword={keyword}&keywordType=2"
                                    f"&jobArea={code}&issuedDate=4&pageNum={pg}&pageSize=20"
                                )
                                page.goto(search_url, timeout=30000, wait_until='commit')
                                time.sleep(random.uniform(1.5, 2.5))
                            except Exception as nav_err:
                                _logger.warning('[%s] 页面重建后导航失败: %s', city, nav_err)
                            _logger.info('[%s] 重建浏览器页面(上次调用超时/断连)', city)
                            page_dead = False
                        try:
                            data, page_dead = _evaluate_with_timeout(page, JS_FETCH_API, params, timeout_ms=28000)
                        except Exception as e:
                            _logger.warning('[%s] 第%d页 evaluate 异常(尝试%d/3): %s', city, pg, attempt + 1, e)
                            data = None
                            if attempt < 2:
                                time.sleep(random.uniform(1.5, 3.0))
                            continue

                        if page_dead:
                            # 页面失效(超时/断连), 下一轮重建页面重试
                            if attempt < 2:
                                _logger.warning('[%s] 第%d页 调用失效(尝试%d/3): %s, 将重建页面重试...',
                                                city, pg, attempt + 1, data.get('error', 'unknown') if isinstance(data, dict) else 'unknown')
                                time.sleep(random.uniform(1.5, 2.5))
                                continue
                            else:
                                _logger.warning('[%s] 第%d页 调用失效(已重试3次), 跳过', city, pg)
                        elif isinstance(data, dict) and 'error' in data:
                            if attempt < 2:
                                _logger.warning('[%s] 第%d页 API 错误(尝试%d/3): %s, 模拟人类行为后重试...',
                                                city, pg, attempt + 1, data['error'])
                                # 重试前模拟人类: 滚到底停留再滚回顶
                                try:
                                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    time.sleep(random.uniform(2.0, 4.0))
                                    page.evaluate("window.scrollTo(0, 0)")
                                    time.sleep(random.uniform(0.5, 1.0))
                                except Exception:
                                    pass
                                time.sleep(random.uniform(1.0, 2.0))
                                continue
                            else:
                                _logger.warning('[%s] 第%d页 API 错误(已重试3次): %s', city, pg, data['error'])
                        break

                    if data is None:
                        break
                    if isinstance(data, dict) and 'error' in data:
                        break

                    job_list = data.get('resultbody', {}).get('job', {}).get('items', [])
                    if not job_list:
                        break
                    pages_collected[city] += 1

                    added = 0
                    for j in job_list:
                        jid = str(j.get('jobId', ''))
                        title = (j.get('jobName') or '').strip()
                        if not jid or not title or jid in all_seen:
                            continue
                        all_seen.add(jid)

                        job_area = (j.get('jobAreaString') or '').strip()
                        if job_area.startswith(city):
                            address = job_area.replace('·', '-')
                        elif job_area:
                            address = city + '-' + job_area.replace('·', '-')
                        else:
                            address = city

                        # 尝试从搜索 API 获取职位描述/标签/福利作为 content
                        content_parts = []
                        desc = (j.get('jobDescription') or j.get('description') or '').strip()
                        if desc:
                            content_parts.append(desc)
                        tags = j.get('jobTags') or []
                        if tags:
                            content_parts.append(' '.join(str(t) for t in tags))
                        welfare = j.get('jobWelfareList') or []
                        if welfare:
                            content_parts.append(' '.join(str(w) for w in welfare))
                        content = ' '.join(content_parts).strip()

                        # 构建51job原始链接
                        # 51job职位URL格式: https://jobs.51job.com/城市拼音/jobId.html
                        job_id = str(j.get('jobId', '') or j.get('jobid', '') or '')
                        city_pinyin = CITY_PINYIN.get(city, city.lower())
                        job_url = f'https://jobs.51job.com/{city_pinyin}/{job_id}.html' if job_id and city_pinyin else ''

                        # 单独提取关键字标签(jobTags),用于后续高频热词统计
                        # 这些标签来自51job,比jieba从描述中分词更精准(如Java/MyBatis/Spring)
                        tags = j.get('jobTags') or []
                        keywords = ' '.join(str(t).strip() for t in tags if str(t).strip()) if tags else ''

                        all_jobs.append({
                            'post': title,
                            'company': (j.get('companyName') or '').strip(),
                            'address': address,
                            'salary_raw': (j.get('provideSalaryString') or '').strip(),
                            'edu': (j.get('degreeString') or '').strip(),
                            'exper': (j.get('workYearString') or '').strip(),
                            'dateT': (j.get('issueDateString') or '').strip(),
                            'scrape_date': now,
                            'content': content,
                            'keywords': keywords,
                            'job_url': job_url,
                        })
                        added += 1

                    _logger.info('[%s] 第%d页: +%d条 (累计 %d)', city, pg, added, len(all_jobs))
                    if progress_callback:
                        progress_callback(city, pg, added)
                    if added == 0:
                        _logger.info('[%s] 第%d页无新数据, 跳过后续页', city, pg)
                        break
            except (KeyboardInterrupt, Exception) as e:
                if isinstance(e, KeyboardInterrupt):
                    _logger.warning('[%s] 采集被中断(Ctrl+C), 保存已采集数据...', city)
                    _interrupted = True
                else:
                    _logger.warning('[%s] 采集异常: %s', city, e)
            finally:
                # 无论正常结束/超时break/Ctrl+C中断,都把该城市已采集的数据写入DB
                if save_callback and len(all_jobs) > city_start_idx:
                    city_jobs = all_jobs[city_start_idx:]
                    try:
                        save_callback(city, city_jobs)
                        _logger.info('[%s] 已增量保存 %d 条到数据库', city, len(city_jobs))
                    except Exception as cb_e:
                        _logger.warning('[%s] 增量保存失败(不影响继续采集): %s', city, cb_e)
            if _interrupted:
                raise KeyboardInterrupt()

        page.close()
        browser.close()

    _logger.info('采集完成: 共 %d 条数据 (关键词=%s, 城市=%s)', len(all_jobs), keyword, cities)
    return all_jobs, pages_collected


if __name__ == '__main__':
    import sqlite3
    import config
    from data.job_store import ensure_schema, upsert_jobs

    jobs, pages_collected = scrape_jobs(
        keyword='python',
        cities=['北京', '上海', '广州', '深圳'],
        pages_per_city=5,
        progress_callback=lambda city, pg, n: print(f'[{city}] 第{pg}页 +{n}条'),
    )
    print(f"\n共采集到 {len(jobs)} 条数据")
    for city, pg_count in pages_collected.items():
        print(f"  {city}: 实际翻取 {pg_count} 页")

    if not jobs:
        print("没有采集到数据,可能是WAF拦截了这次请求")
    else:
        # 命令行与网页 /collect 路由共用同一套落库口径:
        # 建表/补列/唯一索引 + 按 job_url 增量合并(面议排除、重复岗位收敛),
        # 不再清空旧数据 —— 多次采集自然按 collected_at 形成多期数据。
        db = sqlite3.connect(config.DB_PATH)
        ensure_schema(db)
        stats = upsert_jobs(db, jobs)
        db.close()
        print(f"已写入数据库: 新增 {stats['inserted']} 条, 更新 {stats['updated']} 条, "
              f"排除 {stats['skipped']} 条 (保存在 {config.DB_PATH})")
