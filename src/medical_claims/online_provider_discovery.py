"""基于权威原始网页的在线医院/医生发现。

搜索结果只用于定位页面；能力字段与证据均来自随后抓取的原始权威页面。
本模块只接收病种和地区，不接收 OCR 文本或患者身份信息。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import ipaddress
import os
import re
import socket
from threading import Lock
from typing import Protocol
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from .quality_phase1_engine import build_historical_support
from .quality_phase1_models import (
    AssessmentStatus, BasicQualification, CapabilityEvidenceRecord,
    ClinicianCapabilityProfile, ClinicianDiseaseExpertise, ClinicianTreatmentExpertise,
    CurrentCaseMatch, DimensionAssessment, DiseaseCapabilityNeed,
    DiscoveryCandidateDiagnostic, DiscoveryDiagnostics,
    DiseaseSpecialtyCapability, HospitalCapabilityProfile, InstitutionFoundation,
    MedicalResourceCapability, PatientServiceFeedback, PlatformCapability,
    ProviderDiscoveryQuery, ProviderDiscoveryResult, PublicSource,
    RegionalServiceCapability,
)


class SearchHit(tuple):
    __slots__ = ()

    def __new__(cls, title: str, url: str):
        return tuple.__new__(cls, (title, url))

    title = property(lambda self: self[0])
    url = property(lambda self: self[1])


class PublicWebSearch(Protocol):
    def search(self, query: str, limit: int = 8) -> list[SearchHit]: ...


class PageDocument(tuple):
    __slots__ = ()

    def __new__(cls, url: str, title: str, text: str):
        return tuple.__new__(cls, (url, title, text))

    url = property(lambda self: self[0])
    title = property(lambda self: self[1])
    text = property(lambda self: self[2])


class PublicPageFetcher(Protocol):
    def fetch(self, url: str) -> PageDocument: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._ignored = 0
        self._title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        if tag == "title":
            self._title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        if tag == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)
            if self._title:
                self.title_parts.append(value)


def _public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        addresses = socket.getaddrinfo(host, None)
        return bool(addresses) and all(
            not (ipaddress.ip_address(item[4][0]).is_private
                 or ipaddress.ip_address(item[4][0]).is_loopback
                 or ipaddress.ip_address(item[4][0]).is_link_local)
            for item in addresses
        )
    except (OSError, ValueError):
        return False


class BingRssSearch:
    """无密钥的网页发现适配器；RSS 摘要不会写入证据。"""

    endpoint = "https://www.bing.com/search"

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(
                self.endpoint,
                params={"q": query, "format": "rss", "setlang": "zh-cn"},
                headers={"User-Agent": "MedicalClaimsEvidenceDiscovery/1.0"},
            )
            response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        hits = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            url = item.findtext("link") or ""
            if _public_url(url):
                hits.append(SearchHit(title, url))
            if len(hits) >= limit:
                break
        return hits


class SerperWebSearch:
    """可选的 Google Serper 发现适配器；返回摘要仍不会进入证据。"""

    endpoint = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout_seconds: float = 8.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.post(
                self.endpoint,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"q": query, "num": limit, "gl": "cn", "hl": "zh-cn"},
            )
            response.raise_for_status()
        hits = []
        for item in response.json().get("organic", []):
            url = item.get("link") or ""
            if _public_url(url):
                hits.append(SearchHit(item.get("title") or "", url))
        return hits[:limit]


class SoHtmlSearch:
    """360 中文网页发现入口；仅提取结果链接和标题，摘要不进入证据。"""

    endpoint = "https://www.so.com/s"

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self._lock = Lock()

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        with self._lock, httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(
                self.endpoint,
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 MedicalClaimsEvidenceDiscovery/1.0"},
            )
            response.raise_for_status()
        hits: list[SearchHit] = []
        matches = re.finditer(
            r'<h3[^>]*>[\s\S]{0,3000}?<a[^>]+data-mdurl="([^"]+)"[^>]*>([\s\S]*?)</a>',
            response.text,
            re.I,
        )
        for match in matches:
            url = unescape(match.group(1))
            title = re.sub(r"<[^>]+>", "", unescape(match.group(2)))
            title = re.sub(r"\s+", " ", title).strip()
            if _public_url(url):
                hits.append(SearchHit(title, url))
            if len(hits) >= limit:
                break
        return hits


class SogouHtmlSearch:
    """中文网页发现入口；解析跳转目标，结果摘要不进入证据。"""

    endpoint = "https://www.sogou.com/web"

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self._lock = Lock()

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        # 串行访问，避免同一批次并发触发搜索站点限流。
        with self._lock, httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(
                self.endpoint, params={"query": query},
                headers={"User-Agent": "Mozilla/5.0 MedicalClaimsEvidenceDiscovery/1.0"},
            )
            response.raise_for_status()
            if "/antispider/" in str(response.url):
                raise RuntimeError("Sogou rate limited")
            pairs = re.findall(
                r'<h3[^>]*>(.*?)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                response.text, re.S | re.I,
            )
            hits = []
            for prefix, href, title_html in pairs:
                title = re.sub(r"<[^>]+>", "", f"{prefix} {title_html}").strip()
                target = unescape(href)
                if target.startswith("/link?"):
                    redirect = client.get(urljoin(self.endpoint, target))
                    match = re.search(r'window\.location\.replace\(["\']([^"\']+)', redirect.text)
                    if not match:
                        continue
                    target = unescape(match.group(1))
                if _public_url(target) and "sogou.com" not in (urlparse(target).hostname or ""):
                    hits.append(SearchHit(title, target))
                if len(hits) >= limit:
                    break
            return hits


class CompositeWebSearch:
    def __init__(self, providers: list[PublicWebSearch]):
        self.providers = providers

    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        last_error = None
        for provider in self.providers:
            try:
                rows = provider.search(query, limit)
                if rows:
                    return rows
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return []


def build_web_search(timeout_seconds: float) -> PublicWebSearch:
    provider = os.getenv("QUALITY_SEARCH_PROVIDER", "auto").lower()
    api_key = os.getenv("QUALITY_SEARCH_API_KEY", "").strip()
    candidates: list[PublicWebSearch] = []
    if provider in {"auto", "serper"} and api_key:
        candidates.append(SerperWebSearch(api_key, timeout_seconds))
    if provider in {"auto", "so", "360"}:
        candidates.append(SoHtmlSearch(timeout_seconds))
    if provider in {"auto", "sogou"}:
        candidates.append(SogouHtmlSearch(timeout_seconds))
    if provider in {"auto", "bing_rss", "bing"}:
        candidates.append(BingRssSearch(timeout_seconds))
    if not candidates:
        raise ValueError("未配置可用的联网搜索 provider")
    return CompositeWebSearch(candidates)


class HttpxPageFetcher:
    def __init__(self, timeout_seconds: float = 8.0, maximum_bytes: int = 1_500_000):
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes

    def fetch(self, url: str) -> PageDocument:
        if not _public_url(url):
            raise ValueError("拒绝访问非公网地址")
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "MedicalClaimsEvidenceDiscovery/1.0"})
            response.raise_for_status()
        if not _public_url(str(response.url)):
            raise ValueError("重定向目标不是公网地址")
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and "text" not in content_type:
            raise ValueError("只处理公开网页文本")
        raw = response.content[: self.maximum_bytes]
        encoding = response.encoding or "utf-8"
        parser = _TextExtractor()
        parser.feed(raw.decode(encoding, errors="replace"))
        return PageDocument(
            str(response.url), " ".join(parser.title_parts)[:200],
            re.sub(r"\s+", " ", " ".join(parser.parts))[:200_000],
        )


def _authority(page: PageDocument) -> tuple[str, str] | None:
    host = (urlparse(page.url).hostname or "").lower()
    denied_hosts = (
        "39.net", "haodf.com", "familydoctor.com.cn", "fh21.com.cn", "bohe.cn",
        "iiyiyi.com", "236z.com", "hryjz.com", "zhihu.com", "baidu.com",
    )
    if any(host == item or host.endswith(f".{item}") for item in denied_hosts):
        return None
    title_text = f"{page.title} {page.text[:3000]}"
    if host.endswith(".gov.cn"):
        health_hosts = ("wjw", "wsjkw", "health")
        source_type = "卫健委" if any(word in host for word in health_hosts) else "政府公开资料"
        return source_type, page.title or host
    if host.endswith(".edu.cn"):
        return "医学院 / 大学官网", page.title or host
    official_hospital_marker = (
        "【官网】" in page.title
        or "医院官网" in page.title
        or "医院官方网站" in page.text[:3000]
        or bool(re.search(
            r"[\u4e00-\u9fffA-Za-z·]{4,30}医院.{0,50}(?:版权所有|ICP备)",
            page.text[-8000:],
        ))
    )
    if "医院" in page.title and official_hospital_marker:
        return "医院官网", page.title
    return None


def _source_quality(page: PageDocument) -> tuple[str, str, str, float]:
    """按机构权威性、内容客观性和可核实性给原始页面分级。"""
    authority = _authority(page)
    if authority:
        return authority[0], authority[1], "A", 0.95
    host = (urlparse(page.url).hostname or "").lower()
    b_platforms = ("haodf.com", "guahao.com", "91160.com", "zgys.net")
    objective_markers = (
        "科室", "执业", "专业方向", "擅长", "职称", "医院地址", "医院等级", "诊疗项目",
    )
    marketing_markers = ("最好", "第一名", "成功率最高", "保证治愈", "立即咨询", "在线问诊优惠")
    text = f"{page.title} {page.text[:12000]}"
    if (
        any(host == item or host.endswith(f".{item}") for item in b_platforms)
        and any(marker in text for marker in objective_markers)
        and not any(marker in text for marker in marketing_markers)
    ):
        return "正规医疗信息平台", page.title or host, "B", 0.7
    return "发现线索", page.title or host, "C", 0.35


def _looks_like_academic_article(page: PageDocument) -> bool:
    head = f"{page.title} {page.text[:3000]}".lower()
    return any(marker in head for marker in ("摘要", "关键词", "doi", "abstract", "期刊"))


def _short_evidence(text: str, terms: list[str], radius: int = 110) -> str | None:
    for term in terms:
        if not term:
            continue
        index = text.find(term)
        if index >= 0:
            return text[max(0, index - radius): index + len(term) + radius].strip()[:500]
    return None


def _hospital_name(page: PageDocument, query: ProviderDiscoveryQuery | None = None) -> str | None:
    if query and query.recommendation_intent:
        verified_clues = []
        for clue in query.recommendation_intent.candidate_hospital_clues:
            value = re.sub(r"^.*?：", "", clue)
            value = re.sub(r"（[^）]*）", "", value).strip()
            if value.endswith(("医院", "妇幼保健院")) and value in f"{page.title} {page.text[:30000]}":
                verified_clues.append(value)
        if verified_clues:
            return max(verified_clues, key=len)
    pieces = re.split(r"[-_|—]", page.title)
    pattern = re.compile(
        r"([\u4e00-\u9fffA-Za-z·]{2,32}(?:人民医院|中心医院|中医医院|妇幼保健院|附属医院|眼科医院|医院))"
    )
    candidates: list[tuple[str, bool]] = []
    marketing_terms = (
        "全国", "开设", "多家", "城市", "旗下", "集团拥有", "品牌", "连锁",
        "医保定点", "定点医院", "医院名单", "补助", "排名", "哪家医院", "专科医院推荐",
        "去哪家", "治疗哪家", "制定", "完善", "等专科医院",
    )
    leading_noise = ("欢迎光临", "官方网站", "官网", "首页", "走进", "关于")

    for is_title, piece in [(True, item) for item in [*pieces, page.title]] + [(False, page.text[:5000])]:
        for matched in pattern.findall(piece):
            value = re.split(r"[（(]", matched)[0].strip(" -_|—·，。")
            value = re.split(r"[，。；：]", value)[-1]
            value = re.sub(r"【[^】]*】", "", value).strip()
            for prefix in leading_noise:
                if value.startswith(prefix):
                    value = value[len(prefix):].strip()
            if (
                4 <= len(value) <= 30
                and not any(term in value for term in marketing_terms)
                and not re.search(r"\d", value)
            ):
                candidates.append((value, is_title))
    if not candidates:
        return None
    city_prefix = (query.city or "").removesuffix("市") if query else ""
    capability_region = ""
    if query and query.capability_need:
        capability_region = _short_evidence(
            page.text,
            [query.disease_label, *query.capability_need.related_specialty_terms],
            radius=300,
        ) or ""
    unique = dict.fromkeys(candidates)
    return max(unique, key=lambda item: (
        12 * int(bool(city_prefix and city_prefix in item[0]))
        + 10 * int(item[1])
        + 10 * int(item[0] in capability_region)
        + 5 * int("大学" in item[0] or "医学院" in item[0])
        - 15 * int(item[0].startswith("附属"))
        + min(len(item[0]), 30) / 30
    ))[0]


def _capability_context(page: PageDocument, query: ProviderDiscoveryQuery) -> tuple[str | None, list[str]]:
    need = query.capability_need
    if not need:
        return None, []
    text = _short_evidence(
        page.text,
        [query.disease_label, *need.related_specialty_terms, *need.capability_terms],
        radius=320,
    )
    if not text:
        return None, []
    required_matches = [query.disease_label, *need.related_specialty_terms, *need.capability_terms]
    if not any(term and term in text for term in required_matches):
        return None, []
    departments = [term for term in need.related_specialty_terms if term in text]
    capability_cues = ("设有", "开设", "专科", "专病", "诊疗", "诊治", "治疗", "擅长", "手术量", "排名")
    if not departments and not any(cue in text for cue in capability_cues):
        return None, []
    return text, departments


def _entity_bound_capability_context(
    page: PageDocument,
    query: ProviderDiscoveryQuery,
    hospital_name: str,
) -> tuple[str | None, list[str]]:
    """非医院官网必须证明机构名与能力词属于同一连续区域，避免名单页串机构。"""
    need = query.capability_need
    if not need:
        return None, []
    terms = [query.disease_label, *need.related_specialty_terms, *need.capability_terms]
    hospital_pattern = re.compile(
        r"[一-鿿A-Za-z·]{2,32}(?:人民医院|中心医院|中医医院|妇幼保健院|附属医院|眼科医院|医院)"
    )
    primary_terms = [query.disease_label, *need.related_specialty_terms, *need.capability_terms]
    for name_match in re.finditer(re.escape(hospital_name), page.text[:100000]):
        for term in primary_terms:
            if not term:
                continue
            for term_match in re.finditer(re.escape(term), page.text[:100000]):
                if term_match.start() < name_match.start():
                    continue
                if abs(term_match.start() - name_match.start()) > 800:
                    continue
                left, right = sorted((name_match.start(), term_match.start()))
                bridge = page.text[left:right]
                bridge_without_current = bridge.replace(hospital_name, "", 1)
                other_names = {
                    item.strip(" ，。；：") for item in hospital_pattern.findall(bridge_without_current)
                    if hospital_name not in item and item not in hospital_name
                }
                if (
                    other_names
                    or "医院" in bridge_without_current
                    or "妇幼保健院" in bridge_without_current
                    or bridge_without_current.count("。") > 2
                    or bridge_without_current.count("；") > 2
                ):
                    continue
                start = max(0, min(left, term_match.start()) - 180)
                end = min(len(page.text), max(name_match.end(), term_match.end()) + 360)
                region = page.text[start:end].strip()[:900]
                departments = [item for item in need.related_specialty_terms if item in region]
                capability_cues = ("设有", "开设", "专科", "专病", "诊疗", "诊治", "治疗", "擅长", "手术", "中心")
                if departments or any(cue in region for cue in capability_cues):
                    return region, departments
    return None, []


def _page_city(page: PageDocument, query: ProviderDiscoveryQuery) -> str | None:
    for city in [query.city, *query.nearby_major_cities]:
        token = city.removesuffix("市") if city else ""
        if city and (
            city in page.title or city in page.text[:10000]
            or token in page.title or token in page.text[:10000]
        ):
            return city
    matches = re.findall(r"([\u4e00-\u9fff]{2,8}市)", f"{page.title} {page.text[:5000]}")
    blocked = {"人民医院市", "中心医院市"}
    return next((item for item in matches if item not in blocked), None)


def _public_capability_attributes(page: PageDocument, query: ProviderDiscoveryQuery) -> dict:
    text = f"{page.title} {page.text[:30000]}"
    hospital_level = next((item for item in (
        "三级甲等", "三级乙等", "三级眼科医院", "三级医院", "二级甲等", "二级医院"
    ) if item in text), None)
    key_specialties = list(dict.fromkeys(re.findall(
        r"((?:国家级|国家|省级|市级|重庆市)?(?:临床)?重点专科)", text
    )))[:5]
    disease_center = next((item for item in (
        "白内障手术质量控制中心", "白内障诊疗中心", "白内障手术中心",
    ) if item in text), None)
    normalized_text = re.sub(r"\s+", "", text)
    technology_terms = []
    for term in query.capability_need.capability_terms if query.capability_need else []:
        normalized_term = re.sub(r"[\s+（）()]", "", term)
        anchors = [normalized_term]
        if "超声乳化" in normalized_term:
            anchors.append("超声乳化")
        if "人工晶体" in normalized_term or "人工晶状体" in normalized_term:
            anchors.extend(["人工晶体", "人工晶状体"])
        if any(anchor and anchor in normalized_text for anchor in anchors):
            technology_terms.append(term)
    teaching = "大学附属" in text or "医科大学附属" in text
    authority = _authority(page)
    public_source = bool(authority and authority[0] in {
        "卫健委", "政府公开资料", "医学院 / 大学官网",
    })
    specialty_type = "眼科专科医院" if "眼科医院" in page.title else "综合医院" if "附属" in page.title else "医院"
    hospital_type = f"公立{specialty_type}" if public_source else specialty_type
    return {
        "hospital_level": hospital_level,
        "hospital_type": hospital_type,
        "teaching_hospital": teaching,
        "key_specialties": key_specialties,
        "disease_center": disease_center,
        "technology_terms": list(dict.fromkeys(technology_terms)),
    }


def _identifier(prefix: str, *parts: str) -> str:
    return prefix + sha256("|".join(parts).encode("utf-8")).hexdigest()[:16].upper()


def _clinician_mentions(text: str) -> list[tuple[str, str]]:
    excluded = {
        "科室", "门诊", "医院", "医学", "专家", "担任", "现任", "其中", "现有",
        "在职", "硕士", "博士", "医师", "医生",
    }
    found: list[tuple[str, str]] = []
    common_surnames = set(
        "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏"
        "水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷"
        "罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵汪祁毛禹狄米贝明臧"
        "计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵季贾路江童颜郭梅林刁钟徐邱骆高夏蔡田"
        "樊胡凌霍虞万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆"
        "荣翁荀羊惠甄曲封芮储靳焦牧山谷车侯宓蓬全班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘"
        "景詹束龙叶幸司黎乔苍双闻莘党翟谭贡劳姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕"
        "冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘"
        "匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养"
        "鞠须丰巢关蒯相查后荆红游竺权盖益桓公"
    )

    def valid_name(value: str) -> bool:
        return 2 <= len(value) <= 3 and value[0] in common_surnames and value not in excluded

    title_pattern = r"主任医师|副主任医师|主治医师|教授"
    for clinician_name, title in re.findall(
        rf"(?:团队|专家|医生|医师|由|，|、|\s)([\u4e00-\u9fff]{{2,3}})({title_pattern})", text
    ):
        if valid_name(clinician_name):
            found.append((clinician_name, title))
    for title, clinician_name in re.findall(
        rf"({title_pattern})([\u4e00-\u9fff]{{2,3}})(?=[，。；、\s]|擅长|专长|门诊|$)", text
    ):
        if valid_name(clinician_name):
            found.append((clinician_name, title))
    for clinician_name in re.findall(
        r"(?:科主任|副主任)([\u4e00-\u9fff]{2,4}?)(?:主任|医生)?(?:擅长|专长)", text
    ):
        if clinician_name not in excluded:
            found.append((clinician_name, "科室负责人"))
    return list(dict.fromkeys(found))


def _assessment(
    score: float,
    reasoning: str,
    fields: list[str],
    source_id: str,
    confidence: float = 0.78,
) -> DimensionAssessment:
    return DimensionAssessment(
        status=AssessmentStatus.EVALUATED, score=score, confidence=confidence,
        reasoning=reasoning, evidence_fields=fields, source_ids=[source_id],
        limitations=["来自公开网页能力证据，不代表个体疗效或全国排名"],
    )


def _unavailable(reason: str) -> DimensionAssessment:
    return DimensionAssessment(status=AssessmentStatus.INSUFFICIENT_EVIDENCE, reasoning=reason)


class OnlineAuthoritativeProviderDiscovery:
    provider_name = "online_authoritative_public_sources"

    def __init__(
        self,
        search: PublicWebSearch | None = None,
        fetcher: PublicPageFetcher | None = None,
        *,
        max_results_per_query: int = 6,
        max_pages: int = 18,
    ):
        timeout = float(os.getenv("QUALITY_DISCOVERY_TIMEOUT_SECONDS", "8"))
        self.search = search or build_web_search(timeout)
        self.fetcher = fetcher or HttpxPageFetcher(timeout)
        self.max_results_per_query = max_results_per_query
        self.max_pages = max_pages

    def _query_tiers(self, query: ProviderDiscoveryQuery) -> list[list[str]]:
        need = query.capability_need
        specialties = (need.related_specialty_terms if need else [])
        capabilities = (need.capability_terms if need else [])
        terms = " ".join(specialties[:2])
        capability_text = " ".join(capabilities[:3])
        places = list(dict.fromkeys(item for item in [query.city, query.province, *query.nearby_major_cities] if item))
        tiers = []
        if query.city:
            city_token = query.city.removesuffix("市")
            planned = [
                item for item in (
                    query.recommendation_intent.search_queries
                    if query.recommendation_intent else []
                )
                if city_token in item
            ]
            clues = [item for item in (
                query.recommendation_intent.candidate_hospital_clues
                if query.recommendation_intent else []
            ) if city_token in item]
            structured = [item for item in (
                query.recommendation_intent.candidate_hospitals
                if query.recommendation_intent else []
            ) if city_token in item.city]
            tiers.append([
                *planned,
                f"{query.city} {query.disease_label} {terms} {capability_text} 医院 官网",
                f"{query.city} {query.disease_label} {terms} 临床重点专科 site:gov.cn",
                f"{query.city} {query.disease_label} {capability_text} 医院 site:edu.cn",
                *(f"{re.sub(r'^.*?：', '', clue).replace('（眼科）', '')} 眼科 {query.disease_label} 官网" for clue in clues[:5]),
                *(f"{item.hospital_name} {item.department} {query.disease_label} 诊疗项目" for item in structured),
            ])
        for place in places:
            if place == query.city:
                continue
            nearby_requirement = (
                "公立 三级甲等"
                if query.recommendation_intent
                and query.recommendation_intent.nearby_requires_public_tertiary_a
                and place in query.nearby_major_cities
                else ""
            )
            place_token = place.removesuffix("市")
            nearby_clues = [item for item in (
                query.recommendation_intent.candidate_hospital_clues
                if query.recommendation_intent else []
            ) if place_token in item]
            structured = [item for item in (
                query.recommendation_intent.candidate_hospitals
                if query.recommendation_intent else []
            ) if place_token in item.city]
            tiers.append([
                f"{place} {query.disease_label} {terms} {capability_text} {nearby_requirement} 医院 官网",
                f"{place} {query.disease_label} {terms} 临床重点专科 {nearby_requirement} 医院 site:gov.cn",
                f"{place} {query.disease_label} {nearby_requirement} 医院 site:edu.cn",
                *(f"{re.sub(r'^.*?：', '', clue).replace('（眼科）', '')} 眼科 {query.disease_label} 官网" for clue in nearby_clues[:4]),
                *(f"{re.sub(r'^.*?：', '', clue).replace('（眼科）', '')} 公立 三级甲等 医院简介 官网" for clue in nearby_clues[:4]),
                *(f"{item.hospital_name} {item.department} {query.disease_label} 诊疗项目" for item in structured),
            ])
        if query.city and query.nearby_major_cities:
            tiers.append([
                f"{city} {query.disease_label} {terms} {capability_text} 医院 官网"
                for city in query.nearby_major_cities
            ])
        if query.complexity.value == "high" or query.needs_upgrade_referral:
            tiers.append([
                f"{query.disease_label} {terms} {capability_text} 国家临床重点专科 医院 site:gov.cn"
            ])
        return [list(dict.fromkeys(item for item in tier if item.strip())) for tier in tiers if tier]

    def _discover_pages(self, queries: list[str]) -> tuple[list[PageDocument], list[str], list[DiscoveryCandidateDiagnostic]]:
        warnings: list[str] = []
        hits_by_query: list[list[SearchHit]] = []
        diagnostics: list[DiscoveryCandidateDiagnostic] = []
        for search_query in queries:
            try:
                hits = self.search.search(search_query, self.max_results_per_query)
                hits_by_query.append(hits)
                for hit in hits:
                    diagnostics.append(DiscoveryCandidateDiagnostic(
                        search_query=search_query, search_title=hit.title, source_url=hit.url,
                    ))
            except Exception as exc:  # network/provider failure is an explicit, recoverable result
                hits_by_query.append([])
                warnings.append(f"联网检索失败：{type(exc).__name__}")
        urls: list[str] = []
        for rank in range(self.max_results_per_query):
            for hits in hits_by_query:
                if rank < len(hits) and hits[rank].url not in urls:
                    urls.append(hits[rank].url)
                    if len(urls) >= self.max_pages:
                        break
            if len(urls) >= self.max_pages:
                break
        pages: list[PageDocument] = []
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(urls)))) as executor:
            futures = {executor.submit(self.fetcher.fetch, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    page = future.result()
                    pages.append(page)
                    source_type, _, grade, _ = _source_quality(page)
                    for diagnostic in diagnostics:
                        if diagnostic.source_url == url:
                            diagnostic.source_url = page.url
                            diagnostic.page_read = True
                            diagnostic.authority_type = source_type if grade in {"A", "B"} else None
                except Exception as exc:
                    for diagnostic in diagnostics:
                        if diagnostic.source_url == url:
                            diagnostic.decision = "rejected"
                            diagnostic.rejection_reasons = [f"原始页面读取失败：{type(exc).__name__}"]
                    continue
        selected = set(urls)
        for diagnostic in diagnostics:
            if not diagnostic.page_read and diagnostic.source_url not in selected:
                diagnostic.decision = "not_fetched"
                diagnostic.rejection_reasons = ["超过本次原始页面读取上限"]
        if not urls:
            warnings.append("联网检索未返回可读取的候选页面")
        elif not pages:
            warnings.append("已发现候选页面，但权威原始页面均无法读取或验证")
        return pages, list(dict.fromkeys(warnings)), diagnostics

    def _discover_suggested_pages(
        self, query: ProviderDiscoveryQuery
    ) -> tuple[list[PageDocument], list[str], list[DiscoveryCandidateDiagnostic]]:
        """读取 LLM 建议的候选 URL；URL 只是入口，仍执行相同来源分级与事实核验。"""
        intent = query.recommendation_intent
        if not intent:
            return [], [], []
        urls = []
        for candidate in intent.candidate_hospitals:
            values = [candidate.suggested_source_url, *(
                reason.suggested_source_url for reason in candidate.recommendation_reasons
            )]
            for value in values:
                if value and value not in urls:
                    urls.append(value)
        pages: list[PageDocument] = []
        warnings: list[str] = []
        diagnostics = [
            DiscoveryCandidateDiagnostic(
                search_query="DeepSeek候选来源URL（待后端核验）",
                source_url=url,
            )
            for url in urls[: self.max_pages]
        ]
        for diagnostic in diagnostics:
            try:
                page = self.fetcher.fetch(diagnostic.source_url)
                pages.append(page)
                source_type, _, grade, _ = _source_quality(page)
                diagnostic.source_url = page.url
                diagnostic.page_read = True
                diagnostic.authority_type = source_type if grade in {"A", "B"} else None
            except Exception as exc:
                diagnostic.decision = "rejected"
                diagnostic.rejection_reasons = [f"建议来源读取失败：{type(exc).__name__}"]
        if urls and not pages:
            warnings.append("DeepSeek建议的候选来源均无法读取，将继续使用联网搜索发现原始页面")
        return pages, warnings, diagnostics

    def _page_rejection_reasons(self, page: PageDocument, query: ProviderDiscoveryQuery) -> list[str]:
        reasons = []
        need = query.capability_need
        source_type, _, grade, _ = _source_quality(page)
        if grade == "C":
            reasons.append("页面仅达到C级发现线索，不能单独支撑正式推荐理由")
        if _looks_like_academic_article(page):
            reasons.append("学术论文页面不能替代医院官方能力页面")
        name = _hospital_name(page, query)
        if not name:
            reasons.append("无法从原始页面确认完整医院名称")
        if not need:
            reasons.append("未生成病种能力需求")
        else:
            authority = _authority(page)
            capability_text, _ = (
                _capability_context(page, query)
                if authority and authority[0] == "医院官网"
                else _entity_bound_capability_context(page, query, name or "")
            )
            if not capability_text:
                reasons.append("原始页面未找到当前疾病对应的科室、专科或诊疗能力原文")
            else:
                if name and source_type != "医院官网" and name not in page.title and name not in capability_text:
                    reasons.append("医院名称与病种能力原文不在同一可关联页面区域")
        if not _page_city(page, query):
            reasons.append("原始页面未找到可核验的医院城市")
        return reasons

    def _hospital_profile(self, page: PageDocument, query: ProviderDiscoveryQuery) -> HospitalCapabilityProfile | None:
        source_type, source_name, grade, source_confidence = _source_quality(page)
        name = _hospital_name(page, query)
        need = query.capability_need
        if grade == "C" or not name or not need:
            return None
        if _looks_like_academic_article(page):
            return None
        capability_text, departments = (
            _capability_context(page, query)
            if source_type == "医院官网"
            else _entity_bound_capability_context(page, query, name)
        )
        identity_text = _short_evidence(page.text, [name]) or page.title
        city = _page_city(page, query)
        if not capability_text or not city:
            return None
        if source_type != "医院官网" and name not in page.title and name not in capability_text:
            return None
        attributes = _public_capability_attributes(page, query)
        source_id = _identifier("SRC-", page.url)
        source = PublicSource(
            source_id=source_id, title=source_name, url=page.url,
            authority_type=source_type, query_date=datetime.now(timezone.utc).date().isoformat(),
            evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
        )
        hospital_id = _identifier("HOSP-", name)
        records = [
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, "identity"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=identity_text,
                evidence_field="institution_foundation",
                supported_claim=f"该公开页面支持{name}的机构身份及所在地信息。",
                reason="医院身份与所在城市已由公开页面核验。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, "disease_capability"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=capability_text,
                evidence_field="disease_specialty",
                supported_claim=f"该资料支持{name}具备{query.disease_label}相关科室或诊疗能力。",
                reason=f"科室或诊疗方向与{query.disease_label}基本匹配。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, "medical_resources"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=capability_text,
                evidence_field="medical_resources",
                supported_claim=f"该资料支持{name}具有与{query.disease_label}相关的公开医疗资源。",
                reason="公开资料记录了与当前治疗需求相关的诊疗项目或技术。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
        ]
        foundation_score = 72
        if attributes["hospital_level"] == "三级甲等":
            foundation_score += 12
        elif attributes["hospital_level"] and attributes["hospital_level"].startswith("三级"):
            foundation_score += 7
        if attributes["teaching_hospital"]:
            foundation_score += 4
        foundation = InstitutionFoundation(
            hospital_id=hospital_id, hospital_name=name,
            province=query.province if query.province and query.province in page.text else None,
            city=city,
            hospital_level=attributes["hospital_level"], hospital_type=attributes["hospital_type"],
            institution_role=attributes["disease_center"],
            teaching_hospital=attributes["teaching_hospital"] or None,
            assessment=_assessment(
                min(92, foundation_score),
                "机构身份、地区及可获得的等级/教学属性来自已抓取的权威原始页面",
                ["institution_foundation"], source_id,
                source_confidence,
            ),
        )
        specialty_score = min(
            94,
            82 + 5 * bool(attributes["key_specialties"])
            + 5 * bool(attributes["disease_center"])
            + min(4, len(attributes["technology_terms"]) * 2),
        )
        specialty = DiseaseSpecialtyCapability(
            disease_group=query.disease_group, related_departments=departments,
            key_specialties=attributes["key_specialties"],
            key_specialty_level=(attributes["key_specialties"][0] if attributes["key_specialties"] else None),
            disease_specific_center=attributes["disease_center"],
            specialty_capability_description=capability_text,
            specialty_match_level="documented",
            assessment=_assessment(
                specialty_score,
                "原始权威页面明确出现当前病种、相关专科及可核验技术/中心能力",
                ["disease_specialty"], source_id,
                source_confidence,
            ),
        )
        resource_score = min(90, 70 + min(12, len(attributes["technology_terms"]) * 5)
                             + 6 * bool(attributes["disease_center"]))
        resources = MedicalResourceCapability(
            resource_capability_level="documented",
            surgery_or_intervention_capability=("、".join(attributes["technology_terms"]) or None),
            key_equipment_or_technology=attributes["technology_terms"],
            assessment=_assessment(
                resource_score,
                "公开页面提供与当前治疗需求相关的医疗资源或技术证据",
                ["medical_resources"], source_id,
                source_confidence,
            ),
        )
        regional_text = _short_evidence(page.text, ["区域医疗中心", "转诊", "危重症", "急救"])
        regional = RegionalServiceCapability(
            regional_referral_role=regional_text,
            regional_role_level="documented" if regional_text else None,
            assessment=(
                _assessment(72, "原始页面明确说明区域、转诊或危重症服务能力", ["regional_service"], source_id)
                if regional_text else _unavailable("权威页面未提供可核验的区域服务能力")
            ),
        )
        if regional_text:
            records.append(CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, "regional_service"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=regional_text,
                evidence_field="regional_service", supported_claim="该资料支持医院的区域服务或转诊能力。",
                reason="公开资料记录了区域服务或转诊能力。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ))
        return HospitalCapabilityProfile(
            hospital_id=hospital_id, disease_group=query.disease_group,
            institution_foundation=foundation, disease_specialty=specialty,
            medical_resources=resources, regional_service=regional,
            patient_service_feedback=PatientServiceFeedback(assessment=_unavailable("未发现可关联的可靠患者反馈")),
            historical_evidence=build_historical_support(0), sources=[source],
            evidence_records=records, profile_version="hospital-capability-profile-v3-online",
        )

    def discover(self, query: ProviderDiscoveryQuery) -> ProviderDiscoveryResult:
        profiles: dict[str, HospitalCapabilityProfile] = {}
        warnings: list[str] = []
        page_count = 0
        generated_queries = [item for tier in self._query_tiers(query) for item in tier]
        all_diagnostics: list[DiscoveryCandidateDiagnostic] = []
        batches = [self._discover_suggested_pages(query)]
        batches.extend(self._discover_pages(tier) for tier in self._query_tiers(query))
        for pages, tier_warnings, diagnostics in batches:
            warnings.extend(tier_warnings)
            all_diagnostics.extend(diagnostics)
            page_count += len(pages)
            for page in pages:
                profile = self._hospital_profile(page, query)
                if profile:
                    profiles.setdefault(profile.hospital_id, profile)
                    for diagnostic in diagnostics:
                        if diagnostic.source_url == page.url:
                            diagnostic.hospital_name = profile.institution_foundation.hospital_name
                            diagnostic.discovered_specialties = profile.disease_specialty.related_departments
                            diagnostic.evidence_text = profile.disease_specialty.specialty_capability_description
                            diagnostic.decision = "admitted"
                else:
                    reasons = self._page_rejection_reasons(page, query)
                    for diagnostic in diagnostics:
                        if diagnostic.source_url == page.url:
                            diagnostic.hospital_name = _hospital_name(page)
                            diagnostic.decision = "rejected"
                            diagnostic.rejection_reasons = reasons
            enough_total = len(profiles) >= query.minimum_candidates
            if query.nearby_major_cities:
                local_count = sum(
                    item.institution_foundation.city == query.city for item in profiles.values()
                )
                nearby_count = sum(
                    item.institution_foundation.city in query.nearby_major_cities
                    for item in profiles.values()
                )
                local_target = min(
                    query.minimum_candidates,
                    query.recommendation_intent.local_recommendation_limit
                    if query.recommendation_intent else 3,
                )
                enough_geographic_mix = local_count >= local_target and nearby_count >= 2
            else:
                enough_geographic_mix = True
            if enough_total and enough_geographic_mix:
                break
        if not profiles and page_count:
            warnings.append("候选页面未同时满足医院身份、当前病种能力和权威来源门槛")
        return ProviderDiscoveryResult(
            query=query, hospital_profiles=list(profiles.values()),
            discovery_layers=[self.provider_name] if profiles else [],
            dynamic_discovery_attempted=True,
            coverage_complete=len(profiles) >= query.minimum_candidates,
            warnings=list(dict.fromkeys(warnings)),
            diagnostics=DiscoveryDiagnostics(
                disease_label=query.disease_label, city=query.city,
                related_specialties=(query.capability_need.related_specialty_terms if query.capability_need else []),
                generated_queries=generated_queries,
                search_result_count=len(all_diagnostics),
                authoritative_candidate_count=sum(1 for item in all_diagnostics if item.authority_type),
                page_read_count=sum(1 for item in all_diagnostics if item.page_read),
                hospital_identified_count=len({item.hospital_name for item in all_diagnostics if item.hospital_name}),
                admitted_hospital_count=len(profiles), candidates=all_diagnostics,
            ),
        )

    def discover_clinicians(
        self, query: ProviderDiscoveryQuery, hospitals: list[HospitalCapabilityProfile]
    ) -> ProviderDiscoveryResult:
        profiles: dict[str, ClinicianCapabilityProfile] = {}
        warnings: list[str] = []
        for hospital in hospitals[:5]:
            name = hospital.institution_foundation.hospital_name
            need = query.capability_need
            terms = " ".join((need.related_specialty_terms if need else [])[:2])
            capabilities = " ".join((need.capability_terms if need else [])[:3])
            pages: list[PageDocument] = []
            suggested = [item for item in (
                query.recommendation_intent.candidate_clinicians
                if query.recommendation_intent else []
            ) if item.hospital_name == name]
            for source in hospital.sources:
                try:
                    pages.append(self.fetcher.fetch(source.url))
                except Exception as exc:
                    warnings.append(f"医生资料复用医院证据页失败：{type(exc).__name__}")
            for candidate in suggested:
                urls = [candidate.suggested_source_url, *(
                    reason.suggested_source_url for reason in candidate.recommendation_reasons
                )]
                for url in dict.fromkeys(item for item in urls if item):
                    try:
                        pages.append(self.fetcher.fetch(url))
                    except Exception as exc:
                        warnings.append(f"DeepSeek建议医生来源读取失败：{type(exc).__name__}")
            searched_pages, page_warnings, _ = self._discover_pages([
                f"{name} {query.disease_label} {terms} {capabilities} 医生 专家 官网",
                *(f"{name} {item.doctor_name} {item.department} {item.specialty}" for item in suggested),
            ])
            known_urls = {page.url for page in pages}
            pages.extend(page for page in searched_pages if page.url not in known_urls)
            warnings.extend(page_warnings)
            for page in pages:
                source_type, source_name, grade, source_confidence = _source_quality(page)
                if grade == "C":
                    continue
                if name not in page.text and name not in page.title:
                    continue
                evidence_text = _short_evidence(
                    page.text,
                    [query.disease_label, *(need.related_specialty_terms if need else []), *(need.capability_terms if need else [])],
                    radius=320,
                )
                if not evidence_text:
                    continue
                page_excerpt = f"{page.title} {page.text[:20000]}"
                mentions = _clinician_mentions(page_excerpt)
                for candidate in suggested:
                    if candidate.doctor_name in page_excerpt:
                        title_match = re.search(
                            rf"{re.escape(candidate.doctor_name)}.{{0,20}}(主任医师|副主任医师|主治医师|教授)",
                            page_excerpt,
                        )
                        mentions.append((candidate.doctor_name, title_match.group(1) if title_match else "公开职称未确认"))
                candidate_map = {item.doctor_name: item for item in suggested}
                for clinician_name, title in list(dict.fromkeys(mentions))[:3]:
                    clinician = self._clinician_profile(
                        clinician_name, title, page,
                        (source_type, source_name, grade, source_confidence),
                        evidence_text, hospital, query, candidate_map.get(clinician_name),
                    )
                    profiles.setdefault(clinician.clinician_id, clinician)
        if not profiles:
            warnings.append("推荐医院中暂未发现同时满足身份、病种专长和权威来源门槛的医生资料")
        return ProviderDiscoveryResult(
            query=query, clinician_profiles=list(profiles.values()),
            discovery_layers=[self.provider_name] if profiles else [],
            dynamic_discovery_attempted=True, warnings=list(dict.fromkeys(warnings)),
        )

    def _clinician_profile(
        self, clinician_name, title, page, source_quality, evidence_text, hospital, query,
        suggested_candidate=None,
    ):
        source_type, source_name, grade, source_confidence = source_quality
        source_id = _identifier("SRC-", page.url)
        source = PublicSource(
            source_id=source_id, title=source_name, url=page.url,
            authority_type=source_type, query_date=datetime.now(timezone.utc).date().isoformat(),
            evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
        )
        clinician_id = _identifier("DOC-", hospital.hospital_id, clinician_name)
        need = query.capability_need
        department = (
            suggested_candidate.department if suggested_candidate
            else next((term for term in (need.related_specialty_terms if need else []) if term in page.text), "相关专科")
        )
        records = [
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, clinician_name, "identity"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=_short_evidence(page.text, [clinician_name]) or page.title,
                evidence_field="basic_qualification", supported_claim=f"该资料支持{clinician_name}所属医院、科室及公开职称。",
                reason="医生身份、所属医院与科室已由公开资料核验。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, clinician_name, "expertise"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=evidence_text,
                evidence_field="disease_expertise", supported_claim=f"该资料支持{clinician_name}具有{query.disease_label}相关专业方向。",
                reason=f"公开专业方向与{query.disease_label}基本匹配。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
            CapabilityEvidenceRecord(
                evidence_id=_identifier("EVD-", page.url, clinician_name, "technical"), source_id=source_id,
                source_name=source_name, source_type=source_type, source_url=page.url,
                query_date=source.query_date, evidence_text=evidence_text,
                evidence_field="treatment_expertise", supported_claim=f"该资料支持{clinician_name}的相关诊疗专长。",
                reason="公开资料记录了相关诊疗或技术专长。",
                evidence_grade=grade, evidence_confidence=source_confidence, verified=True,
            ),
        ]
        basic = _assessment(75, "医生身份、所属医院与科室来自可核实原始页面", ["basic_qualification"], source_id, source_confidence)
        disease = _assessment(85, "公开专业方向与当前疾病匹配", ["disease_expertise"], source_id, source_confidence)
        treatment = _assessment(76, "公开页面提供当前疾病相关诊疗专长", ["treatment_expertise"], source_id, source_confidence)
        platform = _assessment(75, "所属医院已通过当前病种医院能力门槛", ["basic_qualification"], source_id, source_confidence)
        case_match = _assessment(82, "当前病例明确病种与公开病种专长一致", ["disease_expertise"], source_id, source_confidence)
        return ClinicianCapabilityProfile(
            clinician_id=clinician_id, disease_group=query.disease_group,
            basic_qualification=BasicQualification(
                clinician_id=clinician_id, clinician_name=clinician_name,
                hospital_id=hospital.hospital_id, hospital_name=hospital.institution_foundation.hospital_name,
                department=department, title=title, assessment=basic,
            ),
            disease_expertise=ClinicianDiseaseExpertise(
                disease_group=query.disease_group, disease_expertise=[query.disease_label],
                disease_specific_experience_description=evidence_text,
                specialty_match_level="documented", assessment=disease,
            ),
            treatment_expertise=ClinicianTreatmentExpertise(
                treatment_expertise=[evidence_text], assessment=treatment,
            ),
            platform_capability=PlatformCapability(
                department_strength="所属医院已通过当前病种能力核验", assessment=platform,
            ),
            current_case_match=CurrentCaseMatch(
                disease_match="明确匹配", overall_case_match="病种匹配",
                assessment=case_match,
            ),
            historical_evidence=build_historical_support(0), sources=[source],
            evidence_records=records, profile_version="clinician-capability-profile-v4-online",
        )
