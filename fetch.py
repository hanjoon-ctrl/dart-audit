import re, zipfile, io, json, time
import xml.etree.ElementTree as ET
import requests
from http.server import BaseHTTPRequestHandler

DART_API_KEY = "0f03d7915afd276be0c9e0cd51cf5b15b2861359"
BASE = "https://opendart.fss.or.kr/api"
BIG4 = ["삼일", "삼정", "한영", "안진"]
REPRT_LABEL = {"A001":"사업보고서","A002":"반기보고서","A003":"1분기보고서","A004":"3분기보고서"}
_cache = {}

def load_corp_codes():
    if "corps" in _cache: return _cache["corps"]
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key":DART_API_KEY}, timeout=30)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(zf.read(zf.namelist()[0]))
    listed, unlisted = {}, {}
    for item in root.findall("list"):
        code=item.findtext("corp_code","").strip(); name=item.findtext("corp_name","").strip(); stock=item.findtext("stock_code","").strip()
        if not code or not name: continue
        unlisted[name]=code
        if stock: listed[name]=code
    _cache["corps"]=(listed,unlisted); return listed,unlisted

def find_code(listed,unlisted,name):
    if name in listed: return listed[name],name
    cands=[(n,c) for n,c in listed.items() if name in n or n in name]
    if cands: cands.sort(key=lambda x:abs(len(x[0])-len(name))); return cands[0][1],cands[0][0]
    if name in unlisted: return unlisted[name],name
    return None,None

def get_rcept_no(corp_code,year,rcode):
    r=requests.get(f"{BASE}/list.json",params={"crtfc_key":DART_API_KEY,"corp_code":corp_code,"bgn_de":f"{year}0101","end_de":f"{year}1231","pblntf_detail_ty":rcode,"page_count":"10"},timeout=10)
    d=r.json()
    if d.get("status")=="000" and d.get("list"):
        items=d["list"]; final=[i for i in items if not i.get("rm")] or items
        return final[0]["rcept_no"],final[0]["corp_name"]
    return None,None

def get_doc(rcept_no):
    r=requests.get(f"{BASE}/document.xml",params={"crtfc_key":DART_API_KEY,"rcept_no":rcept_no},timeout=60)
    r.raise_for_status()
    zf=zipfile.ZipFile(io.BytesIO(r.content)); parts=[]
    for fname in sorted(zf.namelist()):
        if not any(fname.lower().endswith(e) for e in (".xml",".htm",".html")): continue
        raw=zf.read(fname)
        for enc in ("utf-8","euc-kr","cp949"):
            try: parts.append(raw.decode(enc)); break
            except: pass
    return "\n".join(parts)

def acode_val(text,acode):
    m=re.search(rf'<T[EUH][^>]+(?:ACODE|AUNIT)="{re.escape(acode)}"[^>]*>(.*?)</T[EUH]>',text,re.DOTALL|re.IGNORECASE)
    if m:
        v=re.sub(r"<[^>]+>","",m.group(1)); return re.sub(r"\s+"," ",v).strip()
    return None

def to_int(s):
    if not s: return None
    n=re.sub(r"[^\d]","",str(s)); return int(n) if n else None

def parse_audit(text):
    res=dict(auditor=None,fee_c=None,hrs_c=None,fee_a=None,hrs_a=None,opinion=None,period=None)
    for ac in ["OPN_AUR1_A","OPN_AUR1_C","SIGK_AUR1"]:
        v=acode_val(text,ac)
        if v and "회계법인" in v: res["auditor"]=re.sub(r"\(주\d+\)","",v).strip(); break
    OP={"1":"적정","2":"한정","3":"부적정","4":"의견거절"}
    for ac in ["OPN_CMT1_A","OPN_CMT1_C","OPN_CMT2_A"]:
        v=acode_val(text,ac)
        if not v: continue
        m2=re.search(rf'<T[EU][^>]+(?:ACODE|AUNIT)="{re.escape(ac)}"[^>]*AUNITVALUE="([^"]*)"',text,re.IGNORECASE)
        if m2 and m2.group(1) in OP: res["opinion"]=OP[m2.group(1)]; break
        if "적정" in v and "부" not in v and "한" not in v: res["opinion"]="적정"; break
        elif "한정" in v: res["opinion"]="한정"; break
        elif "부적정" in v: res["opinion"]="부적정"; break
        elif "거절" in v: res["opinion"]="의견거절"; break
    res["fee_c"]=to_int(acode_val(text,"SIGK_CPAY1")); res["hrs_c"]=to_int(acode_val(text,"SIGK_CTIM1"))
    res["fee_a"]=to_int(acode_val(text,"SIGK_FPAY1")); res["hrs_a"]=to_int(acode_val(text,"SIGK_FTIM1"))
    for k in ["fee_c","fee_a"]:
        if res[k] and not (1<=res[k]<=200000): res[k]=None
    for k in ["hrs_c","hrs_a"]:
        if res[k] and not (1<=res[k]<=500000): res[k]=None
    if not res["auditor"]:
        m=re.search(r"([가-힣]+\s*(?:PwC|KPMG|EY|Deloitte)?\s*회계법인)",text)
        if m: res["auditor"]=m.group(1).strip()
    return res

def do_fetch(body):
    corp_nm=body.get("corp_name",""); sector=body.get("sector","")
    year=body.get("year","2026"); rcode=body.get("rcode","A003"); ftype=body.get("ftype","계약")
    listed,unlisted=load_corp_codes()
    corp_code,matched=find_code(listed,unlisted,corp_nm)
    if not corp_code: return {"ok":False,"corp_name":corp_nm,"msg":"기업코드 미발견"}
    time.sleep(0.2)
    rcept_no,real_name=get_rcept_no(corp_code,year,rcode)
    if not rcept_no: return {"ok":False,"corp_name":matched,"msg":f"{year}년 {REPRT_LABEL.get(rcode,rcode)} 공시 없음"}
    time.sleep(0.2)
    doc=get_doc(rcept_no); info=parse_audit(doc)
    fee=info["fee_c"] if ftype=="계약" else info["fee_a"]
    hours=info["hrs_c"] if ftype=="계약" else info["hrs_a"]
    fph=round(fee*1000000/hours) if fee and hours else None
    auditor=info["auditor"] or "파싱실패"
    return {"ok":True,"sector":sector,"corp_name":real_name or matched,"auditor":auditor,
            "is_big4":any(b in auditor for b in BIG4),"fee":fee,"hours":hours,"fph":fph,
            "opinion":info["opinion"] or "—","period":info["period"] or "—",
            "fee_contract":info["fee_c"],"hours_contract":info["hrs_c"],
            "fee_actual":info["fee_a"],"hours_actual":info["hrs_a"],"rcept_no":rcept_no}

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(length))
            result=do_fetch(body)
        except Exception as e:
            result={"ok":False,"corp_name":"","msg":str(e)}
        self._json(result)
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()
    def _json(self,data):
        body=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(200); self._cors()
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",len(body)); self.end_headers(); self.wfile.write(body)
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
    def log_message(self,*a): pass
