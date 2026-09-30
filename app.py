import streamlit as st
import os
import sqlite3
import pandas as pd
from datetime import datetime

st.set_page_config(page_title='ETF Finder', page_icon='🧭', layout='wide')

st.markdown('''
<style>
.stApp {background:#07100d;color:#f4fbf8}
.block-container{max-width:1500px;padding-top:1rem}
.hero{background:linear-gradient(100deg,#0a2118,#102a20);border:1px solid #00b873;border-left:6px solid #20e99a;padding:18px 22px;border-radius:10px;margin-bottom:14px}
.hero h1{margin:0;color:white;font-size:30px}.hero p{margin:6px 0 0;color:#a9c5ba}
.step{background:#0d1814;border:1px solid #29483e;border-radius:9px;padding:10px 12px;margin:5px 0;color:#dcebe5;font-weight:750}
.result{background:#0d1713;border:1px solid #315b4c;border-left:4px solid #00b873;border-radius:9px;padding:12px;margin:8px 0}
.muted{color:#8fa69d;font-size:12px}
</style>''', unsafe_allow_html=True)

st.markdown('''<div class="hero"><h1>🧭 ETF Finder</h1><p>원하는 투자대상을 따라가면 조건에 맞는 ETF를 찾는 탐색 엔진 </p></div>''', unsafe_allow_html=True)

# 분류체계는 UI/데이터와 분리해 향후 DB 기반으로 교체 가능하게 둔다.
TREE = {
    '국내자산': {
        '주식': {
            '반도체':['전체','메모리','HBM','AI 반도체','파운드리','장비','소재·부품'],
            '2차전지':['전체','셀','양극재','음극재','전해질·분리막','ESS/BESS'],
            '로봇':['전체','휴머노이드','감속기','모터·액추에이터','협동로봇','자동화'],
            '자동차':['전체','완성차','전기차','부품','자율주행'],
            '방산':['전체','항공우주','미사일','지상무기','조선·함정'],
            'AI':['전체','AI 인프라','데이터센터','소프트웨어','전력기기'],
            '바이오':['전체','제약','바이오시밀러','의료기기'],
            '금융':['전체','은행','증권','보험'],
        },
        '채권': {'채권':['국채','회사채','단기채','장기채']},
        '원자재': {'원자재':['금','은','원유','산업금속']},
        '리츠': {'리츠':['국내 리츠','글로벌 리츠']},
    },
    '해외자산': {
        '주식': {
            '반도체':['전체','AI 반도체','메모리·HBM','파운드리','장비'],
            'AI':['전체','빅테크','AI 인프라','데이터센터','소프트웨어'],
            '로봇':['전체','휴머노이드','산업용 로봇','자동화'],
            '에너지':['전체','원전','태양광','풍력','석유·가스'],
            '방산':['전체','미국 방산','글로벌 방산','항공우주'],
            '헬스케어':['전체','바이오','제약','의료기기'],
        },
        '채권': {'채권':['미국 국채','회사채','하이일드','단기채']},
        '원자재': {'원자재':['금','은','원유','구리']},
        '리츠': {'리츠':['미국 리츠','글로벌 리츠']},
    }
}

# V0.1 샘플 스키마. 실제 ETF 결과는 다음 단계의 수집 DB가 연결되기 전에는 생성하지 않는다.
ETF_COLUMNS=['etf_code','etf_name','issuer','asset_region','asset_class','sector','subsector','aum','turnover','fee','index_name','as_of','source','collected_at']
HOLDING_COLUMNS=['etf_code','holding_code','holding_name','weight','quantity','as_of','source','collected_at']

from datetime import timedelta
from pathlib import Path
import json, time, inspect

DATA_DIR=Path(__file__).resolve().parent/'data'
DATA_DIR.mkdir(exist_ok=True)
MASTER_FILE=DATA_DIR/'etf_master.csv'
HOLDINGS_FILE=DATA_DIR/'etf_holdings.csv'
STATUS_FILE=DATA_DIR/'collection_status.json'
DB_FILE=DATA_DIR/'etf_finder.db'

def _secret_or_env(name):
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.getenv(name, '').strip()

def _import_pykrx():
    # pykrx >= 1.2.x는 KRX_ID / KRX_PW 환경변수를 읽어
    # KRX 로그인 세션을 내부에서 자동으로 관리한다.
    krx_id=_secret_or_env('KRX_ID')
    krx_pw=_secret_or_env('KRX_PW')
    if not krx_id or not krx_pw:
        raise RuntimeError('KRX ID와 비밀번호를 먼저 입력해 주세요.')
    os.environ['KRX_ID']=krx_id
    os.environ['KRX_PW']=krx_pw
    try:
        import pykrx
        from pykrx import stock
        ver=getattr(pykrx,'__version__',getattr(pykrx,'version',''))
        return stock, f'pykrx {ver}'.strip()
    except Exception as e:
        raise RuntimeError(
            'pykrx를 불러오지 못했습니다. PowerShell에서 '
            'python -m pip install --upgrade pykrx 를 실행해 주세요.'
        ) from e

def _candidate_dates(days=12):
    d=datetime.now()
    for _ in range(days):
        if d.weekday()<5: yield d.strftime('%Y%m%d')
        d-=timedelta(days=1)

def _find_latest_market_date(stock):
    last_error=None
    for ds in _candidate_dates(20):
        try:
            tickers=stock.get_etf_ticker_list(ds)
            if tickers:
                return ds,list(tickers)
        except Exception as e:
            last_error=e
    msg=str(last_error or '데이터 없음')
    raise RuntimeError(f'최근 ETF 기준일을 확인하지 못했습니다: {msg}')

def _pick_col(df,candidates):
    for c in candidates:
        if c in df.columns: return c
    return None

def _num(v):
    try: return float(str(v).replace(',','').replace('%','').strip())
    except Exception: return None


def _get_etf_pdf(stock, ticker, asof):
    """Installed pykrx signature에 맞춰 ETF PDF를 안전하게 호출."""
    fn = stock.get_etf_portfolio_deposit_file
    try:
        params = list(inspect.signature(fn).parameters.keys())
    except Exception:
        params = []

    if params:
        first = params[0].lower()
        if "date" in first or first in ("fromdate", "trddate"):
            return fn(asof, ticker)
        if "ticker" in first or "code" in first:
            return fn(ticker, asof)

    errors = []
    for args in ((asof, ticker), (ticker, asof)):
        try:
            df = fn(*args)
            if df is not None and not df.empty:
                return df
            errors.append(f"{args}: 빈 DataFrame")
        except Exception as e:
            errors.append(f"{args}: {e}")
    raise RuntimeError("ETF PDF 조회 실패 · " + " | ".join(errors))

def collect_krx_etf_snapshot(progress=None,pause=0.05):
    stock,collector_mode=_import_pykrx()
    asof,tickers=_find_latest_market_date(stock)
    collected_at=datetime.now().isoformat(timespec='seconds')
    master_rows=[]; holding_rows=[]; failures=[]
    try:
        ohlcv=stock.get_etf_ohlcv_by_ticker(asof)
    except Exception:
        ohlcv=pd.DataFrame()

    total=len(tickers)
    consecutive_failures=0
    fail_fast_limit=5

    for i,ticker in enumerate(tickers,1):
        ticker=str(ticker).zfill(6)
        try:
            name=stock.get_etf_ticker_name(ticker) or ticker
            turnover=None
            if not ohlcv.empty and ticker in ohlcv.index and '거래대금' in ohlcv.columns:
                turnover=_num(ohlcv.loc[ticker,'거래대금'])

            pdf=_get_etf_pdf(stock,ticker,asof)
            if pdf is None or pdf.empty:
                raise RuntimeError('PDF 구성종목이 비어 있음')

            d=pdf.reset_index().copy()
            code_col=_pick_col(d,['티커','ticker','종목코드','구성종목코드','index']) or d.columns[0]
            name_col=_pick_col(d,['구성종목명','종목명','name'])
            weight_col=_pick_col(d,['비중','비중(%)','weight'])
            qty_col=_pick_col(d,['계약수','수량','quantity'])
            amount_col=_pick_col(d,['금액','평가금액','amount'])

            amounts=pd.to_numeric(d[amount_col],errors='coerce') if amount_col else None
            amount_total=float(amounts.fillna(0).sum()) if amounts is not None else 0.0
            valid=0

            for _,row in d.iterrows():
                hcode=str(row.get(code_col,'')).strip()
                if hcode.endswith('.0'):
                    hcode=hcode[:-2]
                if hcode.isdigit() and len(hcode)<=6:
                    hcode=hcode.zfill(6)

                hname=str(row.get(name_col,'')).strip() if name_col else ''
                if not hname and hcode.isdigit() and len(hcode)==6:
                    try:
                        hname=stock.get_market_ticker_name(hcode) or hcode
                    except Exception:
                        hname=hcode
                if not hname:
                    hname=hcode or '기타자산'

                w=_num(row.get(weight_col)) if weight_col else None
                if w is None and amount_col and amount_total>0:
                    w=(_num(row.get(amount_col)) or 0.0)/amount_total*100.0
                q=_num(row.get(qty_col)) if qty_col else None

                holding_rows.append({
                    'etf_code':ticker,'holding_code':hcode,'holding_name':hname,
                    'weight':round(float(w or 0),6),'quantity':q,'as_of':asof,
                    'source':'KRX PDF (Portfolio Deposit File)',
                    'collected_at':collected_at
                })
                valid+=1

            if not valid:
                raise RuntimeError('유효 구성종목 없음')

            master_rows.append({
                'etf_code':ticker,'etf_name':name,'issuer':'',
                'asset_region':'','asset_class':'','sector':'','subsector':'',
                'aum':None,'turnover':turnover,'fee':None,'index_name':'',
                'as_of':asof,'source':'KRX Data Marketplace',
                'collected_at':collected_at
            })
            consecutive_failures=0

        except Exception as e:
            failures.append({'etf_code':ticker,'error':str(e)[:600]})
            consecutive_failures+=1
            if not master_rows and consecutive_failures>=fail_fast_limit:
                if progress:
                    progress(i,total,ticker,len(failures))
                sample=' / '.join(
                    f"{x['etf_code']}: {x['error']}" for x in failures[-3:]
                )
                raise RuntimeError(
                    f'ETF 목록 {total:,}개는 조회됐지만 구성종목 조회가 '
                    f'연속 {fail_fast_limit}건 실패해 중단했습니다. 최근 오류: {sample}'
                )

        if progress:
            progress(i,total,ticker,len(failures))
        if pause:
            time.sleep(pause)

    master=pd.DataFrame(master_rows,columns=ETF_COLUMNS)
    holdings=pd.DataFrame(holding_rows,columns=HOLDING_COLUMNS)

    if master.empty or holdings.empty:
        raise RuntimeError('수집 결과가 비어 있어 기존 DB를 변경하지 않았습니다.')

    mt=MASTER_FILE.with_suffix('.tmp')
    ht=HOLDINGS_FILE.with_suffix('.tmp')
    master.to_csv(mt,index=False,encoding='utf-8-sig')
    holdings.to_csv(ht,index=False,encoding='utf-8-sig')
    mt.replace(MASTER_FILE)
    ht.replace(HOLDINGS_FILE)

    status={
        'as_of':asof,'collected_at':collected_at,'total_etfs':total,
        'success_etfs':len(master),'failed_etfs':len(failures),
        'holding_rows':len(holdings),'failures':failures,
        'source':f'KRX Data Marketplace / PDF (Portfolio Deposit File) · {collector_mode}'
    }
    STATUS_FILE.write_text(
        json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8'
    )
    return master,holdings,status


def load_snapshot():
    '''기존 CSV 스냅샷 호환용.'''
    master=pd.read_csv(MASTER_FILE,dtype={'etf_code':str}) if MASTER_FILE.exists() else pd.DataFrame(columns=ETF_COLUMNS)
    holdings=pd.read_csv(HOLDINGS_FILE,dtype={'etf_code':str,'holding_code':str}) if HOLDINGS_FILE.exists() else pd.DataFrame(columns=HOLDING_COLUMNS)
    status=json.loads(STATUS_FILE.read_text(encoding='utf-8')) if STATUS_FILE.exists() else {}
    for df in (master,holdings):
        if 'etf_code' in df:
            df['etf_code']=df['etf_code'].astype(str).str.zfill(6)
    if 'holding_code' in holdings:
        holdings['holding_code']=holdings['holding_code'].fillna('').astype(str)
    return master,holdings,status


# =========================================================
# LOCAL SQLITE DB
# =========================================================

def _db_connect():
    conn=sqlite3.connect(str(DB_FILE),timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def save_local_db(master,holdings):
    '''수집 결과를 로컬 SQLite DB에 저장하고 검색 인덱스를 생성.'''
    if master is None or master.empty or holdings is None or holdings.empty:
        raise RuntimeError('저장할 ETF 데이터가 없습니다.')

    m=master.copy()
    h=holdings.copy()

    for df in (m,h):
        if 'etf_code' in df.columns:
            df['etf_code']=df['etf_code'].astype(str).str.zfill(6)
    if 'holding_code' in h.columns:
        h['holding_code']=h['holding_code'].fillna('').astype(str)
    if 'holding_name' in h.columns:
        h['holding_name']=h['holding_name'].fillna('').astype(str)
    if 'weight' in h.columns:
        h['weight']=pd.to_numeric(h['weight'],errors='coerce').fillna(0.0)

    with _db_connect() as conn:
        m.to_sql('etf_master',conn,if_exists='replace',index=False)
        h.to_sql('etf_holdings',conn,if_exists='replace',index=False)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_master_etf_code ON etf_master(etf_code)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_master_tree ON etf_master(asset_region,asset_class,sector,subsector)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_hold_etf_code ON etf_holdings(etf_code)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_hold_stock_code ON etf_holdings(holding_code)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_hold_stock_name ON etf_holdings(holding_name)')
        conn.commit()


def migrate_existing_csv_to_db():
    '''기존 CSV가 있으면 KRX 재조회 없이 최초 1회 SQLite DB로 변환.'''
    if DB_FILE.exists():
        return False
    if not (MASTER_FILE.exists() and HOLDINGS_FILE.exists()):
        return False

    master,holdings,_=load_snapshot()
    if master.empty or holdings.empty:
        return False

    save_local_db(master,holdings)
    return True


def db_available():
    if not DB_FILE.exists():
        return False
    try:
        with _db_connect() as conn:
            row=conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='etf_master'"
            ).fetchone()
            row2=conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='etf_holdings'"
            ).fetchone()
        return bool(row and row2)
    except Exception:
        return False


def db_token():
    try:
        return DB_FILE.stat().st_mtime_ns
    except Exception:
        return 0


@st.cache_data(show_spinner=False)
def load_master_db(_token):
    if not db_available():
        return pd.DataFrame(columns=ETF_COLUMNS)
    with _db_connect() as conn:
        df=pd.read_sql_query('SELECT * FROM etf_master',conn)
    if 'etf_code' in df.columns:
        df['etf_code']=df['etf_code'].astype(str).str.zfill(6)
    return df


@st.cache_data(show_spinner=False)
def load_holdings_preview_db(_token,limit=500):
    if not db_available():
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    with _db_connect() as conn:
        df=pd.read_sql_query(
            'SELECT * FROM etf_holdings ORDER BY etf_code, weight DESC LIMIT ?',
            conn,params=(int(limit),)
        )
    return df


def local_db_stats():
    if not db_available():
        return {'etfs':0,'holdings':0}
    try:
        with _db_connect() as conn:
            etfs=conn.execute('SELECT COUNT(*) FROM etf_master').fetchone()[0]
            holdings=conn.execute('SELECT COUNT(*) FROM etf_holdings').fetchone()[0]
        return {'etfs':int(etfs or 0),'holdings':int(holdings or 0)}
    except Exception:
        return {'etfs':0,'holdings':0}


def normalize_stock_query(x):
    return str(x or '').strip().replace(' ','').lower()


def reverse_search_etf_db(query_text,min_weight=0.0,require_all=True):
    '''로컬 SQLite에서만 종목 역검색. 이 함수에서는 KRX/인터넷 호출을 하지 않음.'''
    if not db_available():
        return pd.DataFrame()

    targets=[x.strip() for x in str(query_text).split(',') if x.strip()]
    if not targets:
        return pd.DataFrame()

    parts=[]
    with _db_connect() as conn:
        for raw in targets:
            key=normalize_stock_query(raw)

            if key.isdigit():
                code=key.zfill(6)
                z=pd.read_sql_query(
                    '''
                    SELECT etf_code,holding_code,holding_name,weight,as_of,source
                    FROM etf_holdings
                    WHERE holding_code=?
                    ''',
                    conn,params=(code,)
                )
            else:
                z=pd.read_sql_query(
                    '''
                    SELECT etf_code,holding_code,holding_name,weight,as_of,source
                    FROM etf_holdings
                    WHERE LOWER(REPLACE(holding_name,' ',''))=?
                    ''',
                    conn,params=(key,)
                )
                if z.empty and len(key)>=2:
                    z=pd.read_sql_query(
                        '''
                        SELECT etf_code,holding_code,holding_name,weight,as_of,source
                        FROM etf_holdings
                        WHERE LOWER(REPLACE(holding_name,' ','')) LIKE ?
                        ''',
                        conn,params=(f'%{key}%',)
                    )

            if not z.empty:
                z['_target']=raw
                parts.append(z)

    if not parts:
        return pd.DataFrame()

    hits=pd.concat(parts,ignore_index=True)
    hits['weight']=pd.to_numeric(hits['weight'],errors='coerce').fillna(0.0)

    agg=(hits.groupby('etf_code',as_index=False)
             .agg(match_weight=('weight','sum'),
                  match_count=('_target','nunique'),
                  matched_stocks=('holding_name',lambda x:', '.join(dict.fromkeys(map(str,x)))),
                  as_of=('as_of','max'),
                  source=('source',lambda x:' / '.join(dict.fromkeys(str(v) for v in x if pd.notna(v))))))

    if require_all:
        agg=agg[agg['match_count']>=len(targets)]

    agg=agg[agg['match_weight']>=float(min_weight)]
    if agg.empty:
        return pd.DataFrame()

    codes=agg['etf_code'].astype(str).tolist()
    placeholders=','.join('?' for _ in codes)
    sql=(
        'SELECT etf_code,etf_name,issuer,aum,turnover,fee,index_name '
        f'FROM etf_master WHERE etf_code IN ({placeholders})'
    )
    with _db_connect() as conn:
        master=pd.read_sql_query(sql,conn,params=codes)

    out=agg.merge(master,on='etf_code',how='left')
    order=[
        c for c in [
            'etf_name','etf_code','issuer','match_weight','matched_stocks',
            'match_count','aum','turnover','fee','index_name','as_of','source'
        ] if c in out.columns
    ]
    return (
        out.sort_values(['match_weight','match_count'],ascending=[False,False])
           [order]
           .reset_index(drop=True)
    )


# 기존 CSV가 있다면 네트워크 접속 없이 SQLite로 1회 변환
try:
    _migrated=migrate_existing_csv_to_db()
except Exception:
    _migrated=False

_status={}
try:
    if STATUS_FILE.exists():
        _status=json.loads(STATUS_FILE.read_text(encoding='utf-8'))
except Exception:
    _status={}

_token=db_token()
master=load_master_db(_token)
_stats=local_db_stats()


# =========================================================
# LOCAL DB STATUS / MANUAL UPDATE
# =========================================================

st.subheader('💾 로컬 ETF 데이터베이스')

_s1,_s2,_s3,_s4=st.columns(4)
_s1.metric('저장 ETF',f"{_stats['etfs']:,}")
_s2.metric('구성종목',f"{_stats['holdings']:,}")
_s3.metric('기준일',_status.get('as_of','-'))
_s4.metric('DB 방식','SQLite')

if _migrated:
    st.success('기존 CSV 데이터를 로컬 SQLite DB로 자동 변환했습니다. KRX 재조회는 하지 않았습니다.')

if db_available():
    st.success('검색 모드: 로컬 DB 사용 중 · 일반 검색 시 KRX에 접속하지 않습니다.')
else:
    st.info('아직 로컬 DB가 없습니다. 아래 버튼을 한 번 실행해 최초 DB를 생성하세요.')

_krx_ready=bool(_secret_or_env('KRX_ID') and _secret_or_env('KRX_PW'))
if _krx_ready:
    st.caption('🔐 KRX 로그인 정보: .streamlit/secrets.toml에서 자동 사용')
else:
    st.warning('KRX 업데이트용 Secrets가 없습니다. .streamlit/secrets.toml에 KRX_ID와 KRX_PW를 저장해 주세요.')

with st.expander('🔄 KRX에서 로컬 DB 새로고침',expanded=not db_available()):
    st.caption(
        '이 버튼을 누를 때만 KRX 전체 데이터를 조회합니다. '
        '저장 완료 후 종목검색·테크트리·ETF 조회는 모두 로컬 DB에서 실행됩니다.'
    )

    if st.button('KRX 전체 데이터로 로컬 DB 업데이트',type='primary',use_container_width=True):
        if not _krx_ready:
            st.error('secrets.toml의 KRX_ID / KRX_PW를 먼저 확인해 주세요.')
            st.stop()

        bar=st.progress(0,text='KRX ETF 데이터를 로컬 DB용으로 수집하는 중...')

        def _progress(i,total,ticker,failed):
            bar.progress(
                min(i/max(total,1),1.0),
                text=f'{i:,}/{total:,} · {ticker} · 실패 {failed:,}건'
            )

        try:
            _m,_h,_status=collect_krx_etf_snapshot(progress=_progress)
            save_local_db(_m,_h)
            st.cache_data.clear()

            bar.progress(1.0,text='로컬 DB 저장 완료')
            st.success(
                f"기준일 {_status['as_of']} · ETF {_status['success_etfs']:,}개 · "
                f"구성종목 {_status['holding_rows']:,}건을 etf_finder.db에 저장했습니다."
            )
            st.rerun()
        except Exception as e:
            st.error(f'업데이트 실패: {e}')

st.divider()


# =========================================================
# TREE SEARCH - LOCAL DB ONLY
# =========================================================

left,right=st.columns([0.82,1.45],gap='large')

with left:
    st.subheader('🌳 ETF 테크트리')
    region=st.radio('STEP 1 · 투자대상',list(TREE.keys()),horizontal=True)
    asset=st.selectbox('STEP 2 · 자산군',list(TREE[region].keys()))
    sector=st.selectbox('STEP 3 · 산업/테마',list(TREE[region][asset].keys()))
    subsector=st.selectbox('STEP 4 · 세부분야',TREE[region][asset][sector])
    st.markdown(
        f'<div class="step">{region} → {asset} → {sector} → {subsector}</div>',
        unsafe_allow_html=True
    )
    st.caption('선택/검색 과정에서는 KRX에 접속하지 않습니다.')

with right:
    st.subheader('🔎 조건에 맞는 ETF')
    if master.empty:
        st.info('로컬 ETF DB가 비어 있습니다. 최초 1회 DB 업데이트가 필요합니다.')
    else:
        q=master.copy()
        tree_cols=['asset_region','asset_class','sector','subsector']
        mapped=(
            all(c in q.columns for c in tree_cols)
            and q['asset_region'].fillna('').astype(str).str.len().gt(0).any()
        )
        if mapped:
            q=q[(q.asset_region==region)&(q.asset_class==asset)&(q.sector==sector)]
            if subsector!='전체':
                q=q[q.subsector==subsector]
            if q.empty:
                st.info('현재 로컬 DB에는 이 테크트리 분류로 매핑된 ETF가 없습니다.')
            else:
                st.dataframe(q,use_container_width=True,hide_index=True)
        else:
            st.info('실제 ETF 데이터는 저장되어 있지만 테크트리 분류 매핑은 아직 비어 있습니다. 종목 역검색은 바로 사용할 수 있습니다.')

st.divider()


# =========================================================
# REVERSE STOCK SEARCH - LOCAL DB ONLY
# =========================================================

a,b=st.columns([1,1.25],gap='large')

with a:
    st.subheader('🔍 종목으로 ETF 찾기')
    st.caption('로컬 DB에서만 검색하므로 전체 ETF를 다시 조회하지 않습니다.')

    names=st.text_input(
        '종목명 / 종목코드',
        placeholder='예: SK하이닉스 또는 000660 · 복수검색: 삼성전자, SK하이닉스'
    )

    c1,c2=st.columns(2)
    with c1:
        min_weight=st.number_input(
            '최소 합산 편입비중 (%)',
            min_value=0.0,max_value=100.0,value=0.0,step=0.5
        )
    with c2:
        match_mode=st.selectbox('복수 종목 조건',['모두 포함','하나 이상 포함'])

    if st.button('ETF 역검색',type='primary',use_container_width=True):
        if not db_available():
            st.warning('로컬 ETF DB가 없습니다. 최초 1회 DB 업데이트가 필요합니다.')
        else:
            with st.spinner('로컬 DB 검색 중...'):
                result=reverse_search_etf_db(
                    names,
                    min_weight=min_weight,
                    require_all=(match_mode=='모두 포함')
                )

            if result.empty:
                st.info('조건에 맞는 ETF를 찾지 못했습니다. 종목명/코드 또는 최소 편입비중을 확인해 주세요.')
            else:
                show=result.rename(columns={
                    'etf_name':'ETF명',
                    'etf_code':'ETF코드',
                    'issuer':'운용사',
                    'match_weight':'합산 편입비중(%)',
                    'matched_stocks':'일치 종목',
                    'match_count':'일치 종목수',
                    'aum':'순자산',
                    'turnover':'거래대금',
                    'fee':'총보수',
                    'index_name':'추종지수',
                    'as_of':'구성 기준일',
                    'source':'출처'
                })

                st.success(f'{len(show):,}개 ETF를 찾았습니다. 로컬 DB 검색 결과입니다.')
                st.dataframe(
                    show,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        '합산 편입비중(%)':st.column_config.NumberColumn(format='%.2f%%')
                    }
                )

with b:
    st.subheader('⚡ 검색 구조')
    st.markdown('''
- **최초 1회 / 수동 업데이트:** KRX → 로컬 DB 저장
- **종목 역검색:** 로컬 SQLite DB만 조회
- **테크트리 필터:** 로컬 ETF Master만 조회
- **Streamlit 재실행:** 저장된 DB를 다시 사용
- **검색할 때마다 1,000개 이상 ETF를 재수집하지 않음**
''')
    st.caption(f'로컬 DB: {DB_FILE.name} · 앱 실행 시각: {datetime.now():%Y-%m-%d %H:%M:%S}')

st.divider()


# =========================================================
# LOCAL DB VIEW
# =========================================================

st.subheader('🗄️ 로컬 ETF DB')
t1,t2=st.tabs(['ETF Master','ETF Holdings 미리보기'])

with t1:
    if master.empty:
        st.info('저장된 ETF Master 데이터가 없습니다.')
    else:
        st.dataframe(master,use_container_width=True,hide_index=True)

with t2:
    if not db_available():
        st.info('저장된 ETF Holdings 데이터가 없습니다.')
    else:
        preview=load_holdings_preview_db(db_token(),500)
        st.caption(f"전체 {_stats['holdings']:,}건 중 최대 500건만 미리 표시합니다. 검색은 전체 DB를 대상으로 합니다.")
        st.dataframe(preview,use_container_width=True,hide_index=True)
