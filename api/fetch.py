from http.server import BaseHTTPRequestHandler
import json, re, zipfile, io, time, os
import xml.etree.ElementTree as ET
import requests as rq

DART_API_KEY = "0f03d7915afd276be0c9e0cd51cf5b15b2861359"
BASE = "https://opendart.fss.or.kr/api"
BIG4 = ["삼일", "삼정", "한영", "안진"]
REPRT_LABEL = {"A001":"사업보고서","A002":"반기보고서","A003":"1분기보고서","A004":"3분기보고서"}

# corp_codes.json 로드 (사전 생성 파일 - XML 다운로드 불필요)
_CORP_CODES = None
def get_corp_codes():
    global _CORP_CODES
    if _CORP_CODES is not None:
        return _CORP_CODES
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "corp_codes.json")
    with open(path, encoding="utf-8") as f:
        _CORP_CODES = json.load(f)
    return _CORP_CODES

def find_code(name):
    codes = get_corp_codes()
    if name in codes:
        return codes[name], name
    # 부분 매칭
    cands = [(n,c) for n,c in codes.items() if name in n or n in name]
    if cands:
        cands.sort(key=lambda x: abs(len(x[0])-len(name)))
        return cands[0][1], cands[0][0]
    return None, None

def get_rcept(code, year, rcode):
    r = rq.get(f"{BASE}/list.json", params={
        "crtfc_key": DART_API_KEY, "corp_code": code,
        "bgn_de": f"{year}0101", "end_de": f"{year}1231",
        "pblntf_detail_ty": rcode, "page_count": "10",
    }, timeout=15)
    d = r.json()
    if d.get("status") == "000" and d.get("list"):
        items = d["list"]
        final = [i for i in items if not i.get("rm")] or items
        return final[0]["rcept_no"], final[0]["corp_name"]
    return None, None

def get_doc(rcept_no):
    r = rq.get(f"{BASE}/document.xml", params={
        "crtfc_key": DART_API_KEY, "rcept_no": rcept_no,
    }, timeout=55)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    parts = []
    for fn in sorted(zf.namelist()):
        if not any(fn.lower().endswith(e) for e in (".xml",".htm",".html")): continue
        raw = zf.read(fn)
        for enc in ("utf-8","euc-kr","cp949"):
            try: parts.append(raw.decode(enc)); break
            except: pass
    return "\n".join(parts)

def av(text, ac):
    m = re.search(rf'<T[EUH][^>]+(?:ACODE|AUNIT)="{re.escape(ac)}"[^>]*>(.*?)</T[EUH]>',
                  text, re.DOTALL|re.IGNORECASE)
    if m: return re.sub(r"\s+"," ",re.sub(r"<[^>]+>","",m.group(1))).strip()
    return None

def ti(s):
    if not s: return None
    n = re.sub(r"[^\d]","",str(s))
    return int(n) if n else None

def parse(text):
    res = dict(auditor=None,fc=None,hc=None,fa=None,ha=None,opinion=None)
    for ac in ["OPN_AUR1_A","OPN_AUR1_C","SIGK_AUR1"]:
        v = av(text, ac)
        if v and "회계법인" in v:
            res["auditor"] = re.sub(r"\(주\d+\)","",v).strip(); break
    OP = {"1":"적정","2":"한정","3":"부적정","4":"의견거절"}
    for ac in ["OPN_CMT1_A","OPN_CMT1_C","OPN_CMT2_A"]:
        v = av(text, ac)
        if not v: continue
        m2 = re.search(rf'AUNIT="{re.escape(ac)}"[^>]*AUNITVALUE="([^"]*)"', text, re.IGNORECASE)
        if m2 and m2.group(1) in OP: res["opinion"] = OP[m2.group(1)]; break
        if   "적정" in v and "부" not in v and "한" not in v: res["opinion"]="적정"; break
        elif "한정"  in v: res["opinion"]="한정"; break
        elif "부적정" in v: res["opinion"]="부적정"; break
        elif "거절"  in v: res["opinion"]="의견거절"; break
    res["fc"]=ti(av(text,"SIGK_CPAY1")); res["hc"]=ti(av(text,"SIGK_CTIM1"))
    res["fa"]=ti(av(text,"SIGK_FPAY1")); res["ha"]=ti(av(text,"SIGK_FTIM1"))
    for k in ["fc","fa"]:
        if res[k] and not (1<=res[k]<=200000): res[k]=None
    for k in ["hc","ha"]:
        if res[k] and not (1<=res[k]<=500000): res[k]=None
    if not res["auditor"]:
        m = re.search(r"([가-힣]+\s*(?:PwC|KPMG|EY|Deloitte)?\s*회계법인)", text)
        if m: res["auditor"] = m.group(1).strip()
    return res


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_POST(self):
        try:
            body  = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            nm    = body.get("corp_name","")
            sector= body.get("sector","")
            year  = body.get("year","2026")
            rcode = body.get("rcode","A003")
            ftype = body.get("ftype","계약")

            code, matched = find_code(nm)
            if not code:
                return self._res({"ok":False,"corp_name":nm,"msg":"기업코드 미발견"})

            rno, rname = get_rcept(code, year, rcode)
            if not rno:
                return self._res({"ok":False,"corp_name":matched,
                                  "msg":f"{year}년 {REPRT_LABEL.get(rcode,rcode)} 공시 없음"})

            info   = parse(get_doc(rno))
            fee    = info["fc"] if ftype=="계약" else info["fa"]
            hours  = info["hc"] if ftype=="계약" else info["ha"]
            fph    = round(fee*1000000/hours) if fee and hours else None
            auditor= info["auditor"] or "파싱실패"

            self._res({"ok":True,"sector":sector,"corp_name":rname or matched,
                "auditor":auditor,"is_big4":any(b in auditor for b in BIG4),
                "fee":fee,"hours":hours,"fph":fph,
                "opinion":info["opinion"] or "—","period":"—",
                "fee_contract":info["fc"],"hours_contract":info["hc"],
                "fee_actual":info["fa"],"hours_actual":info["ha"],"rcept_no":rno})
        except Exception as e:
            self._res({"ok":False,"corp_name":"","msg":str(e)})

    def _res(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200); self._cors()
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers(); self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")

    def log_message(self,*a): pass
