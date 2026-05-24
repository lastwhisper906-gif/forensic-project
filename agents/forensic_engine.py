"""forensic_engine.py — 10-K 기계적 Diff 엔진 (Session 3/5 기반).

LLM 호출 없이 Python만으로:
  1. N vs N-1 10-K 섹션 텍스트 Diff (단어/문장 수준)
  2. Forensic 위험 키워드 탐지 및 컨텍스트 추출
  3. 변화 분류 (추가/삭제/수정/톤 변화)

Agent 4 (diff_analyzer.py) 가 이 결과를 받아 LLM으로 의미 해석.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


# ===========================================================================
# 1) 데이터 구조
# ===========================================================================

@dataclass
class TextChange:
    """단일 텍스트 변화 단위."""
    change_type: str          # "added" | "removed" | "modified" | "context"
    old_text:    str | None
    new_text:    str | None
    line_no_old: int | None = None
    line_no_new: int | None = None
    forensic_keywords: list[str] = field(default_factory=list)


@dataclass
class SectionDiff:
    """단일 10-K 섹션의 Diff 결과."""
    section_name:    str
    ticker:          str
    fy_current:      str        # "2024-01-28" 형식 (10-K period)
    fy_prior:        str
    changes:         list[TextChange] = field(default_factory=list)
    # 집계 지표
    lines_added:     int = 0
    lines_removed:   int = 0
    lines_modified:  int = 0
    change_ratio:    float = 0.0     # 변화된 텍스트 비율 (0~1)
    # Forensic 탐지 결과
    keyword_hits:    list[dict] = field(default_factory=list)
    has_risk_signal: bool = False
    # 원본 텍스트 (LLM 전달용)
    text_old: str = ""
    text_new: str = ""
    # 차이가 있는 블록만 추출 (토큰 절약)
    diff_chunks: list[str] = field(default_factory=list)


@dataclass
class ForensicDiffReport:
    """전체 10-K Diff 리포트 (5개 섹션 통합)."""
    ticker:        str
    fy_current:    str
    fy_prior:      str
    sections:      dict[str, SectionDiff] = field(default_factory=dict)
    # 집계
    total_changes: int = 0
    high_risk_sections: list[str] = field(default_factory=list)
    top_keywords:  list[tuple[str, int]] = field(default_factory=list)  # (keyword, count)
    # 체크섬 (캐시 키용)
    content_hash:  str = ""


# ===========================================================================
# 2) Forensic 키워드 사전
# ===========================================================================

# 위험 키워드 → 위험도 레벨 매핑
_FORENSIC_KEYWORDS: dict[str, str] = {
    # HIGH — 즉각 조사 필요
    "restatement":                    "HIGH",
    "restated":                       "HIGH",
    "material weakness":              "HIGH",
    "going concern":                  "HIGH",
    "non-reliance":                   "HIGH",
    "fraud":                          "HIGH",
    "irregularities":                 "HIGH",
    "investigation":                  "HIGH",
    "sec inquiry":                    "HIGH",
    "sec investigation":              "HIGH",
    "class action":                   "HIGH",
    "whistleblower":                  "HIGH",

    # HIGH — 회계 정책 변경
    "useful life":                    "HIGH",
    "estimated useful life":          "HIGH",
    "depreciation period":            "HIGH",
    "impairment":                     "HIGH",
    "goodwill impairment":            "HIGH",
    "write-off":                      "HIGH",
    "write-down":                     "HIGH",

    # MEDIUM — 수익 인식 / 채널
    "bill-and-hold":                  "MEDIUM",
    "channel stuffing":               "MEDIUM",
    "sell-through":                   "MEDIUM",
    "consignment":                    "MEDIUM",
    "revenue recognition":            "MEDIUM",
    "performance obligation":         "MEDIUM",
    "variable consideration":         "MEDIUM",
    "contract modification":          "MEDIUM",

    # MEDIUM — 현금 흐름 조작
    "factoring":                      "MEDIUM",
    "securitization":                 "MEDIUM",
    "receivables purchase":           "MEDIUM",
    "supplier financing":             "MEDIUM",
    "off-balance sheet":              "MEDIUM",

    # MEDIUM — 관계사 / 특수 목적 법인
    "related party":                  "MEDIUM",
    "variable interest entity":       "MEDIUM",
    r"\bVIE\b":                       "MEDIUM",
    "special purpose":                "MEDIUM",
    "affiliate transaction":          "MEDIUM",

    # MEDIUM — 세그먼트 / 공시
    "segment realignment":            "MEDIUM",
    "segment reclassification":       "MEDIUM",
    "reallocation":                   "MEDIUM",
    "discontinued operations":        "MEDIUM",

    # LOW — 톤 변화 신호어 (hedging language 증가)
    "we believe":                     "LOW",
    "we cannot guarantee":            "LOW",
    "no assurance":                   "LOW",
    "may not":                        "LOW",
    "could adversely":                "LOW",
    "subject to change":              "LOW",
    "preliminary":                    "LOW",
}

# 섹션별 주요 관심 패턴 (정규식)
_SECTION_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "critical_accounting": [
        (r"useful\s+li(?:fe|ves).{0,80}(?:\d+)\s*(?:year|month)", "HIGH"),
        (r"(?:server|network|gpu|data\s+center).{0,30}(?:\d+)\s*(?:to|-)\s*(?:\d+)\s*year", "HIGH"),
        (r"(?:change|revision|update).{0,30}useful\s+li(?:fe|ves)", "HIGH"),
        (r"discount\s+rate.{0,20}\d+(?:\.\d+)?%", "MEDIUM"),
        (r"impairment\s+test.{0,50}assumption", "MEDIUM"),
    ],
    "risk_factors": [
        (r"(?:delete|remov|eliminat).{0,40}risk", "HIGH"),      # 톤 소프트닝 패턴
        (r"no\s+longer\s+(?:believ|consider|expect)", "MEDIUM"),
        (r"(?:material|significant).{0,20}(?:customer|supplier)", "MEDIUM"),
    ],
    "related_party": [
        (r"(?:new|additional|entered).{0,30}(?:agreement|transaction|arrangement)", "HIGH"),
        (r"\$\s*\d+(?:\.\d+)?\s*(?:million|billion|M|B)\b", "MEDIUM"),
        (r"(?:increase|grew|higher).{0,20}\d+%", "MEDIUM"),
    ],
}


# ===========================================================================
# 3) 핵심 Diff 함수
# ===========================================================================

def _normalize_text(text: str) -> list[str]:
    """텍스트 → 정규화된 라인 리스트.

    - 연속 공백 축소
    - 빈 줄 최대 1개로 제한
    - 페이지 번호 / 헤더 제거 (간이)
    """
    lines: list[str] = []
    prev_blank = False
    for raw_line in text.splitlines():
        line = re.sub(r"\s{2,}", " ", raw_line).strip()
        # 페이지 번호처럼 보이는 라인 제거 (숫자만, 또는 'Page N of M')
        if re.match(r"^(?:Page\s+)?\d+(?:\s+of\s+\d+)?$", line, re.I):
            continue
        if line:
            lines.append(line)
            prev_blank = False
        else:
            if not prev_blank:
                lines.append("")
            prev_blank = True
    return lines


def _detect_forensic_keywords(text: str, section_name: str = "") -> list[dict]:
    """텍스트에서 Forensic 키워드 + 섹션별 패턴 탐지.

    Returns:
        [{
          "keyword":  str,
          "severity": "HIGH"|"MEDIUM"|"LOW",
          "context":  str,   # ±150자 컨텍스트
          "position": int,
        }]
    """
    hits: list[dict] = []
    text_lower = text.lower()

    # 일반 키워드
    for kw, severity in _FORENSIC_KEYWORDS.items():
        try:
            pat = re.compile(kw, re.I)
        except re.error:
            pat = re.compile(re.escape(kw), re.I)
        for m in pat.finditer(text):
            s = max(0, m.start() - 150)
            e = min(len(text), m.end() + 150)
            hits.append({
                "keyword":  kw.replace(r"\b", ""),
                "severity": severity,
                "context":  text[s:e].replace("\n", " ").strip(),
                "position": m.start(),
            })

    # 섹션별 패턴
    for pat_str, severity in _SECTION_PATTERNS.get(section_name, []):
        for m in re.finditer(pat_str, text, re.I):
            s = max(0, m.start() - 150)
            e = min(len(text), m.end() + 150)
            hits.append({
                "keyword":  m.group()[:50],
                "severity": severity,
                "context":  text[s:e].replace("\n", " ").strip(),
                "position": m.start(),
            })

    # 중복 제거 (위치 기반)
    seen: set[int] = set()
    unique: list[dict] = []
    for h in sorted(hits, key=lambda x: x["position"]):
        pos_bucket = h["position"] // 50
        if pos_bucket not in seen:
            seen.add(pos_bucket)
            unique.append(h)
    return unique


def compute_section_diff(
    text_old: str,
    text_new: str,
    section_name: str = "",
    ticker: str = "",
    fy_current: str = "",
    fy_prior: str = "",
    context_lines: int = 4,
    min_change_length: int = 20,   # 이 길이 미만의 변화는 noise 처리
) -> SectionDiff:
    """두 10-K 섹션 텍스트를 비교하여 SectionDiff 반환.

    Args:
        text_old:           FY N-1 텍스트
        text_new:           FY N 텍스트
        section_name:       섹션 식별자 (risk_factors, mda, ...)
        ticker:             종목 티커
        fy_current:         현재 회계연도 기간
        fy_prior:           직전 회계연도 기간
        context_lines:      diff chunk 전후 컨텍스트 라인 수
        min_change_length:  noise 필터 최소 변화 길이

    Returns:
        SectionDiff — 변화 목록 + 집계 통계 + keyword_hits
    """
    lines_old = _normalize_text(text_old)
    lines_new = _normalize_text(text_new)

    diff = SectionDiff(
        section_name=section_name,
        ticker=ticker,
        fy_current=fy_current,
        fy_prior=fy_prior,
        text_old=text_old[:8000],   # 토큰 절약: 최대 8,000자
        text_new=text_new[:8000],
    )

    # difflib 시퀀스 매처
    matcher = difflib.SequenceMatcher(
        None, lines_old, lines_new,
        autojunk=False,
    )

    # 변화 블록 수집
    diff_chunks: list[str] = []
    changed_chars_old = 0
    changed_chars_new = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        old_block = "\n".join(lines_old[i1:i2])
        new_block = "\n".join(lines_new[j1:j2])

        # noise 필터
        if tag == "replace":
            if len(old_block) < min_change_length and len(new_block) < min_change_length:
                continue

        # 컨텍스트 라인 추출 (LLM 전달용)
        ctx_before = lines_old[max(0, i1 - context_lines): i1]
        ctx_after  = lines_new[j2: j2 + context_lines]
        chunk_lines = ["--- CHANGE ---"]
        if ctx_before:
            chunk_lines.append("[CONTEXT BEFORE]")
            chunk_lines.extend(ctx_before)
        if tag in ("replace", "delete") and old_block:
            chunk_lines.append("[REMOVED]")
            chunk_lines.append(old_block)
        if tag in ("replace", "insert") and new_block:
            chunk_lines.append("[ADDED]")
            chunk_lines.append(new_block)
        if ctx_after:
            chunk_lines.append("[CONTEXT AFTER]")
            chunk_lines.extend(ctx_after)
        diff_chunks.append("\n".join(chunk_lines))

        # 변화 분류
        if tag == "replace":
            change_type = "modified"
            diff.lines_modified += 1
        elif tag == "delete":
            change_type = "removed"
            diff.lines_removed += max(1, i2 - i1)
        else:  # insert
            change_type = "added"
            diff.lines_added += max(1, j2 - j1)

        changed_chars_old += len(old_block)
        changed_chars_new += len(new_block)

        tc = TextChange(
            change_type=change_type,
            old_text=old_block if old_block else None,
            new_text=new_block if new_block else None,
            line_no_old=i1,
            line_no_new=j1,
        )
        diff.changes.append(tc)

    # change_ratio 계산
    total_old = sum(len(l) for l in lines_old) or 1
    diff.change_ratio = round(changed_chars_old / total_old, 4)

    # Forensic 키워드 탐지 — 변화된 부분에서 우선 탐지
    changed_text = "\n".join(
        (c.old_text or "") + "\n" + (c.new_text or "")
        for c in diff.changes
    )
    diff.keyword_hits = _detect_forensic_keywords(changed_text, section_name)

    # Risk signal 여부
    diff.has_risk_signal = any(h["severity"] == "HIGH" for h in diff.keyword_hits)
    diff.diff_chunks = diff_chunks[:30]   # 최대 30 청크

    return diff


def generate_forensic_diff_report(
    ticker: str,
    sections_current: dict[str, str],   # {section_name: text}
    sections_prior:   dict[str, str],
    fy_current: str = "",
    fy_prior:   str = "",
) -> ForensicDiffReport:
    """모든 입력 섹션에 대한 Diff 리포트 생성.

    sections_current / sections_prior 에 있는 모든 섹션을 처리.
    섹션 이름은 자유형식 (risk_factors, item_1a_risk_factors, ppe_useful_life_note 등).

    Args:
        ticker:            종목 티커
        sections_current:  현재 연도 섹션 dict
        sections_prior:    직전 연도 섹션 dict
        fy_current:        현재 회계연도 기간 문자열
        fy_prior:          직전 회계연도 기간 문자열

    Returns:
        ForensicDiffReport — 섹션별 SectionDiff + 집계 정보
    """
    # 두 dict의 모든 섹션 이름을 합집합으로 처리 (순서 유지)
    all_sections: list[str] = list(
        dict.fromkeys(list(sections_current.keys()) + list(sections_prior.keys()))
    )

    report = ForensicDiffReport(
        ticker=ticker,
        fy_current=fy_current,
        fy_prior=fy_prior,
    )

    keyword_counter: dict[str, int] = {}

    for sec in all_sections:
        text_curr = sections_current.get(sec, "")
        text_prev = sections_prior.get(sec, "")

        if not text_curr and not text_prev:
            continue

        sec_diff = compute_section_diff(
            text_old=text_prev,
            text_new=text_curr,
            section_name=sec,
            ticker=ticker,
            fy_current=fy_current,
            fy_prior=fy_prior,
        )
        report.sections[sec] = sec_diff

        report.total_changes += sec_diff.lines_added + sec_diff.lines_removed + sec_diff.lines_modified

        if sec_diff.has_risk_signal:
            report.high_risk_sections.append(sec)

        for hit in sec_diff.keyword_hits:
            kw = hit["keyword"]
            keyword_counter[kw] = keyword_counter.get(kw, 0) + 1

    # 상위 키워드
    report.top_keywords = sorted(keyword_counter.items(), key=lambda x: x[1], reverse=True)[:10]

    # 컨텐츠 해시 (캐시 키용)
    raw = f"{ticker}{fy_current}{fy_prior}" + "".join(
        (sections_current.get(s, "") + sections_prior.get(s, ""))[:500]
        for s in all_sections
    )
    report.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

    return report


def get_diff_summary_for_llm(report: ForensicDiffReport, max_chars: int = 12000) -> str:
    """ForensicDiffReport → LLM 전달용 요약 문자열.

    변화가 없는 부분은 제외하고 핵심 변화만 압축.
    토큰 예산: max_chars 이내로 자름.
    """
    parts: list[str] = []
    parts.append(
        f"## 10-K Diff Summary: {report.ticker}\n"
        f"FY Prior: {report.fy_prior} → FY Current: {report.fy_current}\n"
        f"Total changes: {report.total_changes}  |  "
        f"High-risk sections: {', '.join(report.high_risk_sections) or 'none'}\n"
    )

    for sec_name, sec_diff in report.sections.items():
        if not sec_diff.changes:
            continue

        parts.append(f"\n### Section: {sec_name}")
        parts.append(
            f"Added: {sec_diff.lines_added}  Removed: {sec_diff.lines_removed}  "
            f"Modified: {sec_diff.lines_modified}  ChangeRatio: {sec_diff.change_ratio:.1%}"
        )

        if sec_diff.keyword_hits:
            high_hits = [h for h in sec_diff.keyword_hits if h["severity"] == "HIGH"]
            if high_hits:
                parts.append(f"⚠️ HIGH keywords: {[h['keyword'] for h in high_hits[:5]]}")

        # diff chunks (섹션당 최대 4개)
        for chunk in sec_diff.diff_chunks[:4]:
            parts.append(chunk[:800])

    result = "\n".join(parts)
    return result[:max_chars]
