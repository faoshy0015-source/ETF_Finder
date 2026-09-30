import streamlit as st
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
import json, time

DATA_DIR=Path(__file__).resolve().parent/'data'
DATA_DIR.mkdir(exist_ok=True)
MASTER_FILE=DATA_DIR/'etf_master.csv'
HOLDINGS_FILE=DATA_DIR/'etf_holdings.csv'
STATUS_FILE=DATA_DIR/'collection_status.json'

def _import_pykrx():
    try:
        from pykrx import stock
        return stock
    except Exception as e:
        raise RuntimeError('pykrx가 설치되지 않았습니다. PowerShell에서 python -m pip install pykrx 를 실행해 주세요.') from e

def _candidate_dates(days=12):
    d=datetime.now()
    for _ in range(days):
        if d.weekday()<5: yield d.strftime('%Y%m%d')
        d-=timedelta(days=1)

def _find_latest_market_date(stock):
    last_error=None
    for ds in _candidate_dates(15):
        try:
            tickers=stock.get_etf_ticker_list(ds)
            if tickers: return ds,list(tickers)
        except Exception as e: last_error=e
    raise RuntimeError(f'최근 ETF 기준일을 확인하지 못했습니다: {last_error or "데이터 없음"}')

def _pick_col(df,candidates):
    for c in candidates:
        if c in df.columns: return c
    return None

def _num(v):
    try: return float(str(v).replace(',','').replace('%','').strip())
    except Exception: return None

def collect_krx_etf_snapshot(progress=None,pause=0.05):
    stock=_import_pykrx(); asof,tickers=_find_latest_market_date(stock)
    collected_at=datetime.now().isoformat(timespec='seconds')
    master_rows=[]; holding_rows=[]; failures=[]
    try: ohlcv=stock.get_etf_ohlcv_by_ticker(asof)
    except Exception: ohlcv=pd.DataFrame()
    total=len(tickers)
    for i,ticker in enumerate(tickers,1):
        ticker=str(ticker).zfill(6)
        try:
            name=stock.get_etf_ticker_name(ticker) or ticker
            turnover=None
            if not ohlcv.empty and ticker in ohlcv.index and '거래대금' in ohlcv.columns: turnover=_num(ohlcv.loc[ticker,'거래대금'])
            pdf=stock.get_etf_portfolio_deposit_file(ticker,asof)
            if pdf is None or pdf.empty: raise RuntimeError('PDF 구성종목이 비어 있음')
            d=pdf.reset_index().copy()
            code_col=_pick_col(d,['티커','ticker','종목코드','index']) or d.columns[0]
            name_col=_pick_col(d,['종목명','name']); weight_col=_pick_col(d,['비중','비중(%)','weight'])
            qty_col=_pick_col(d,['계약수','수량','quantity']); amount_col=_pick_col(d,['금액','평가금액','amount'])
            amounts=pd.to_numeric(d[amount_col],errors='coerce') if amount_col else None
            amount_total=float(amounts.fillna(0).sum()) if amounts is not None else 0.0
            valid=0
            for _,row in d.iterrows():
                hcode=str(row.get(code_col,'')).strip()
                if hcode.endswith('.0'): hcode=hcode[:-2]
                if hcode.isdigit() and len(hcode)<=6: hcode=hcode.zfill(6)
                hname=str(row.get(name_col,'')).strip() if name_col else ''
                if not hname and hcode.isdigit() and len(hcode)==6:
                    try: hname=stock.get_market_ticker_name(hcode) or hcode
                    except Exception: hname=hcode
                if not hname: hname=hcode or '기타자산'
                w=_num(row.get(weight_col)) if weight_col else None
                if w is None and amount_col and amount_total>0: w=(_num(row.get(amount_col)) or 0.0)/amount_total*100.0
                q=_num(row.get(qty_col)) if qty_col else None
                holding_rows.append({'etf_code':ticker,'holding_code':hcode,'holding_name':hname,'weight':round(float(w or 0),6),'quantity':q,'as_of':asof,'source':'KRX PDF (Portfolio Deposit File)','collected_at':collected_at}); valid+=1
            if not valid: raise RuntimeError('유효 구성종목 없음')
            master_rows.append({'etf_code':ticker,'etf_name':name,'issuer':'','asset_region':'','asset_class':'','sector':'','subsector':'','aum':None,'turnover':turnover,'fee':None,'index_name':'','as_of':asof,'source':'KRX Data Marketplace','collected_at':collected_at})
        except Exception as e: failures.append({'etf_code':ticker,'error':str(e)[:300]})
        if progress: progress(i,total,ticker,len(failures))
        if pause: time.sleep(pause)
    master=pd.DataFrame(master_rows,columns=ETF_COLUMNS); holdings=pd.DataFrame(holding_rows,columns=HOLDING_COLUMNS)
    if master.empty or holdings.empty: raise RuntimeError('수집 결과가 비어 있어 기존 DB를 변경하지 않았습니다.')
    mt=MASTER_FILE.with_suffix('.tmp'); ht=HOLDINGS_FILE.with_suffix('.tmp')
    master.to_csv(mt,index=False,encoding='utf-8-sig'); holdings.to_csv(ht,index=False,encoding='utf-8-sig'); mt.replace(MASTER_FILE); ht.replace(HOLDINGS_FILE)
    status={'as_of':asof,'collected_at':collected_at,'total_etfs':total,'success_etfs':len(master),'failed_etfs':len(failures),'holding_rows':len(holdings),'failures':failures,'source':'KRX Data Marketplace / PDF (Portfolio Deposit File)'}
    STATUS_FILE.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    return master,holdings,status

def load_snapshot():
    master=pd.read_csv(MASTER_FILE,dtype={'etf_code':str}) if MASTER_FILE.exists() else pd.DataFrame(columns=ETF_COLUMNS)
    holdings=pd.read_csv(HOLDINGS_FILE,dtype={'etf_code':str,'holding_code':str}) if HOLDINGS_FILE.exists() else pd.DataFrame(columns=HOLDING_COLUMNS)
    status=json.loads(STATUS_FILE.read_text(encoding='utf-8')) if STATUS_FILE.exists() else {}
    for df in (master,holdings):
        if 'etf_code' in df: df['etf_code']=df['etf_code'].astype(str).str.zfill(6)
    if 'holding_code' in holdings: holdings['holding_code']=holdings['holding_code'].fillna('').astype(str)
    return master,holdings,status


if 'etf_master' not in st.session_state or 'etf_holdings' not in st.session_state:
    _m,_h,_status=load_snapshot()
    st.session_state.etf_master=_m
    st.session_state.etf_holdings=_h
    st.session_state.collection_status=_status

st.subheader('🔄 국내 ETF 실제 데이터 수집')
_u1,_u2=st.columns([1,1.7])
with _u1:
    if st.button('KRX ETF 구성종목 DB 업데이트',type='primary',use_container_width=True):
        bar=st.progress(0,text='KRX ETF 목록을 확인하는 중...')
        def _progress(i,total,ticker,failed):
            bar.progress(min(i/max(total,1),1.0),text=f'{i:,}/{total:,} · {ticker} · 실패 {failed:,}건')
        try:
            _m,_h,_status=collect_krx_etf_snapshot(progress=_progress)
            st.session_state.etf_master=_m
            st.session_state.etf_holdings=_h
            st.session_state.collection_status=_status
            bar.progress(1.0,text='수집 완료')
            st.success(f"기준일 {_status['as_of']} · ETF {_status['success_etfs']:,}개 · 구성종목 {_status['holding_rows']:,}건 저장 완료")
        except Exception as e:
            st.error(f'수집 실패: {e}')
with _u2:
    _status=st.session_state.get('collection_status',{})
    if _status:
        c1,c2,c3,c4=st.columns(4)
        c1.metric('기준일',_status.get('as_of','-'))
        c2.metric('정상 ETF',f"{_status.get('success_etfs',0):,}")
        c3.metric('구성종목',f"{_status.get('holding_rows',0):,}")
        c4.metric('수집 실패',f"{_status.get('failed_etfs',0):,}")
        st.caption(f"출처: {_status.get('source','KRX')} · 수집시각: {_status.get('collected_at','-')}")
        if _status.get('failures'):
            with st.expander('⚠️ 수집 실패 ETF 확인'):
                st.dataframe(pd.DataFrame(_status['failures']),use_container_width=True,hide_index=True)
    else:
        st.info('아직 저장된 실제 ETF 스냅샷이 없습니다. 왼쪽 업데이트 버튼을 눌러 최초 DB를 생성하세요.')

st.divider()

left,right=st.columns([0.82,1.45],gap='large')
with left:
    st.subheader('🌳 ETF 테크트리')
    region=st.radio('STEP 1 · 투자대상', list(TREE.keys()), horizontal=True)
    asset=st.selectbox('STEP 2 · 자산군', list(TREE[region].keys()))
    sector=st.selectbox('STEP 3 · 산업/테마', list(TREE[region][asset].keys()))
    subsector=st.selectbox('STEP 4 · 세부분야', TREE[region][asset][sector])
    st.markdown(f'<div class="step">{region} → {asset} → {sector} → {subsector}</div>',unsafe_allow_html=True)
    st.caption('테크트리 분류와 실제 ETF 편입 데이터는 분리 저장합니다.')

with right:
    st.subheader('🔎 조건에 맞는 ETF')
    master=st.session_state.etf_master
    if master.empty:
        st.info('아직 실데이터 수집기가 연결되지 않았습니다. 잘못된 ETF를 임의로 표시하지 않고, 다음 단계에서 공식 데이터 기반 DB를 연결합니다.')
        st.markdown('''**결과 카드에 표시할 항목**  
ETF명 · 코드 · 운용사 · 순자산 · 거래대금 · 총보수 · 추종지수 · 관련도 · 구성종목 기준일 · 데이터 출처''')
    else:
        q=master[(master.asset_region==region)&(master.asset_class==asset)&(master.sector==sector)]
        if subsector!='전체': q=q[q.subsector==subsector]
        st.dataframe(q,use_container_width=True,hide_index=True)

st.divider()
a,b=st.columns([1,1.25],gap='large')
def normalize_stock_query(x):
    return str(x or '').strip().replace(' ', '').lower()

def reverse_search_etf(master, holdings, query_text, min_weight=0.0, require_all=True):
    """종목명 또는 종목코드로 ETF를 역검색하고 실제 편입비중 순으로 정렬한다."""
    if holdings is None or holdings.empty:
        return pd.DataFrame()
    targets=[x.strip() for x in str(query_text).split(',') if x.strip()]
    if not targets:
        return pd.DataFrame()
    h=holdings.copy()
    h['weight']=pd.to_numeric(h['weight'],errors='coerce').fillna(0.0)
    h['_name_key']=h['holding_name'].map(normalize_stock_query)
    h['_code_key']=h['holding_code'].astype(str).str.strip().str.lower()
    target_keys=[normalize_stock_query(x) for x in targets]
    parts=[]
    for raw,key in zip(targets,target_keys):
        # 정확한 종목명/코드 우선, 종목명 부분일치는 보조로 허용
        m=(h['_name_key']==key)|(h['_code_key']==key)
        if not m.any() and len(key)>=2:
            m=h['_name_key'].str.contains(key,regex=False,na=False)
        z=h[m].copy()
        if not z.empty:
            z['_target']=raw
            parts.append(z)
    if not parts:
        return pd.DataFrame()
    hits=pd.concat(parts,ignore_index=True)
    agg=(hits.groupby('etf_code',as_index=False)
             .agg(match_weight=('weight','sum'),
                  match_count=('_target','nunique'),
                  matched_stocks=('holding_name',lambda x:', '.join(dict.fromkeys(map(str,x)))),
                  as_of=('as_of','max'),
                  source=('source',lambda x:' / '.join(dict.fromkeys(str(v) for v in x if pd.notna(v))))))
    if require_all:
        agg=agg[agg['match_count']>=len(targets)]
    agg=agg[agg['match_weight']>=float(min_weight)]
    if master is not None and not master.empty:
        cols=[c for c in ['etf_code','etf_name','issuer','aum','turnover','fee','index_name'] if c in master.columns]
        agg=agg.merge(master[cols].drop_duplicates('etf_code'),on='etf_code',how='left')
    order=[c for c in ['etf_name','etf_code','issuer','match_weight','matched_stocks','match_count','aum','turnover','fee','index_name','as_of','source'] if c in agg.columns]
    return agg.sort_values(['match_weight','match_count'],ascending=[False,False])[order].reset_index(drop=True)

with a:
    st.subheader('🔍 종목으로 ETF 찾기')
    st.caption('종목명 또는 6자리 종목코드를 입력하면 실제 편입비중이 높은 ETF부터 찾습니다.')
    names=st.text_input('종목명 / 종목코드',placeholder='예: SK하이닉스 또는 000660 · 복수검색: 삼성전자, SK하이닉스')
    c1,c2=st.columns(2)
    with c1:
        min_weight=st.number_input('최소 합산 편입비중 (%)',min_value=0.0,max_value=100.0,value=0.0,step=0.5)
    with c2:
        match_mode=st.selectbox('복수 종목 조건',['모두 포함','하나 이상 포함'])
    if st.button('ETF 역검색',type='primary',use_container_width=True):
        result=reverse_search_etf(
            st.session_state.etf_master, st.session_state.etf_holdings, names,
            min_weight=min_weight, require_all=(match_mode=='모두 포함')
        )
        if st.session_state.etf_holdings.empty:
            st.warning('아직 실제 ETF 구성종목 DB가 비어 있습니다. 다음 단계에서 KRX/운용사 PDF 수집기를 연결하면 이 검색창이 즉시 실데이터로 동작합니다.')
        elif result.empty:
            st.info('조건에 맞는 ETF를 찾지 못했습니다. 종목명/코드 또는 최소 편입비중을 확인해 주세요.')
        else:
            show=result.rename(columns={
                'etf_name':'ETF명','etf_code':'ETF코드','issuer':'운용사',
                'match_weight':'합산 편입비중(%)','matched_stocks':'일치 종목',
                'match_count':'일치 종목수','aum':'순자산','turnover':'거래대금',
                'fee':'총보수','index_name':'추종지수','as_of':'구성 기준일','source':'출처'
            })
            st.success(f'{len(show):,}개 ETF를 찾았습니다. 편입비중 높은 순입니다.')
            st.dataframe(show,use_container_width=True,hide_index=True,
                         column_config={'합산 편입비중(%)':st.column_config.NumberColumn(format='%.2f%%')})
with b:
    st.subheader('🛡️ 데이터 신뢰도')
    st.markdown('''
- **구성종목 기준일(as_of)**과 **수집시각(collected_at)**을 별도 저장
- ETF/편입종목 데이터마다 **source** 저장
- 이전 구성종목을 덮어쓰지 않고 날짜별 스냅샷 보존
- 갱신 실패 시 오래된 데이터를 최신 데이터처럼 표시하지 않음
- 공식 데이터와 운용사 자료를 교차검증할 수 있도록 수집 계층 분리
''')
    st.caption(f'앱 실행 시각: {datetime.now():%Y-%m-%d %H:%M:%S}')

st.divider()
st.subheader('🗄️ V0.3 실제 데이터베이스')
t1,t2=st.tabs(['ETF Master','ETF Holdings'])
with t1: st.dataframe(st.session_state.etf_master,use_container_width=True,hide_index=True)
with t2: st.dataframe(st.session_state.etf_holdings,use_container_width=True,hide_index=True)
