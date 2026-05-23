"""
DART OpenAPI → KOSPI/KOSDAQ 전체 상장사 목록 수집
- 100개씩 배치 처리, 배치 사이 1초 대기
- 실패 시 3회 재시도 (지수 백오프)
- 체크포인트 저장으로 중단 후 재개 가능
"""
import zipfile
import io
import json
import xml.etree.ElementTree as ET
import pandas as pd
import time
from pathlib import Path
from curl_cffi import requests as curl_req

API_KEY     = '5e67214e2ada4027d32b8fd208cd9025a07b5be5'
IMPERSONATE = 'chrome110'
MARKET_MAP  = {'Y': 'KOSPI', 'K': 'KOSDAQ'}
BATCH_SIZE  = 100
RETRY_MAX   = 3

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / 'data'
OUT_CSV    = DATA_DIR / 'company_list.csv'
CKPT_FILE  = DATA_DIR / 'fetch_checkpoint.json'


# ── 체크포인트 ────────────────────────────────────────────────────────────────
def load_checkpoint():
    if CKPT_FILE.exists():
        with open(CKPT_FILE, encoding='utf-8') as f:
            ck = json.load(f)
        print(f"[재개] 체크포인트 발견: {ck['completed']}개 완료, {len(ck['companies'])}개 수집")
        return ck['completed_codes'], ck['companies']
    return set(), []


def save_checkpoint(completed_codes, companies):
    tmp = CKPT_FILE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'completed': len(completed_codes),
                   'completed_codes': list(completed_codes),
                   'companies': companies}, f, ensure_ascii=False)
    tmp.replace(CKPT_FILE)


# ── 1. 기업코드 XML 다운로드 ─────────────────────────────────────────────────
def download_corp_codes():
    print("DART 기업코드 ZIP 다운로드 중...", flush=True)
    url = 'https://opendart.fss.or.kr/api/corpCode.xml'

    for attempt in range(RETRY_MAX):
        try:
            resp = curl_req.get(url, params={'crtfc_key': API_KEY},
                                timeout=120, impersonate=IMPERSONATE)
            if resp.status_code == 200 and resp.content[:4] == b'PK\x03\x04':
                break
            print(f"  시도 {attempt+1}: HTTP {resp.status_code} — {resp.content[:100]}", flush=True)
        except Exception as e:
            print(f"  시도 {attempt+1}/{RETRY_MAX} 실패: {e}", flush=True)
        if attempt < RETRY_MAX - 1:
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError("corpCode.xml 다운로드 모두 실패")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_data = zf.read(zf.namelist()[0])

    root  = ET.fromstring(xml_data)
    corps = []
    for item in root.findall('list'):
        sc = (item.findtext('stock_code') or '').strip()
        if len(sc) == 6:
            corps.append({
                'corp_code':  item.findtext('corp_code').strip(),
                'corp_name':  item.findtext('corp_name').strip(),
                'stock_code': sc,
            })

    print(f"  종목코드 보유 기업: {len(corps):,}개\n", flush=True)
    return corps


# ── 2. 단일 기업 상세 조회 (3회 재시도) ──────────────────────────────────────
def get_company_info(corp_code):
    url    = 'https://opendart.fss.or.kr/api/company.json'
    params = {'crtfc_key': API_KEY, 'corp_code': corp_code}
    for attempt in range(RETRY_MAX):
        try:
            resp = curl_req.get(url, params=params, timeout=15,
                                impersonate=IMPERSONATE)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == '000':
                    return data
            elif resp.status_code == 429:
                time.sleep(5)
        except Exception:
            pass
        if attempt < RETRY_MAX - 1:
            time.sleep(2 ** attempt)   # 1s → 2s
    return None


# ── 3. 메인 ────────────────────────────────────────────────────────────────
def main():
    DATA_DIR.mkdir(exist_ok=True)

    corps = download_corp_codes()

    # 체크포인트에서 이어 시작
    done_codes, companies = load_checkpoint()
    remaining = [c for c in corps if c['corp_code'] not in done_codes]

    total     = len(corps)
    completed = len(done_codes)

    print(f"수집 대상: {len(remaining):,}개 (전체 {total:,}개, 이미 완료 {completed:,}개)", flush=True)
    print(f"배치 크기: {BATCH_SIZE}개, 배치 간 1초 대기\n", flush=True)

    # 100개씩 배치 처리
    for batch_start in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE

        for corp in batch:
            info = get_company_info(corp['corp_code'])
            if info and info.get('corp_cls') in MARKET_MAP:
                companies.append({
                    '회사명':   info.get('corp_name', corp['corp_name']).strip(),
                    '종목코드': corp['stock_code'],
                    '업종':    info.get('induty_code', '').strip(),
                    '시장구분': MARKET_MAP[info['corp_cls']],
                    'DART코드': corp['corp_code'],
                })
            done_codes.add(corp['corp_code'])
            completed += 1

        # 배치 완료 후 체크포인트 저장
        save_checkpoint(done_codes, companies)
        pct = completed / total * 100
        print(f"  배치 {batch_num:>4}/{total_batches}  "
              f"({completed:,}/{total:,}, {pct:.1f}%)  "
              f"수집: {len(companies):,}개", flush=True)

        # 마지막 배치가 아니면 1초 대기
        if batch_start + BATCH_SIZE < len(remaining):
            time.sleep(1)

    # 최종 CSV 저장
    df = (pd.DataFrame(companies)
            .sort_values(['시장구분', '종목코드'])
            .reset_index(drop=True))

    df.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')

    # 체크포인트 삭제 (완료)
    if CKPT_FILE.exists():
        CKPT_FILE.unlink()

    print(f"\n{'='*55}", flush=True)
    print(f"저장 완료: {OUT_CSV}", flush=True)
    print(f"총 {len(df):,}개 기업\n", flush=True)
    print(df['시장구분'].value_counts().to_string(), flush=True)
    print("\n[샘플 5행]", flush=True)
    print(df.head(5).to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
