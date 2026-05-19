export const config = { runtime: "edge" };

const BASE = "https://opendart.fss.or.kr/api";
const KEY  = "0f03d7915afd276be0c9e0cd51cf5b15b2861359";
const BIG4 = ["삼일","삼정","한영","안진"];
const REPRT = {A001:"사업보고서",A002:"반기보고서",A003:"1분기보고서",A004:"3분기보고서"};

// make_corp_codes.py 결과 기반 정확한 코드
const CODES = {
  "LG생활건강":"00356370","아모레퍼시픽":"00547258","코스맥스":"00166928",
  "한국콜마":"00254882","클리오":"01065000","잇츠한불":"00573978",
  "토니모리":"01196869","에이블씨엔씨":"00651138","에스디생명공학":"00877672",
  "코스메카코리아":"00926321","씨앤씨인터내셔널":"01536452","SK바이오랜드":"00164887",
  "나우코스":"01270660","아이패밀리에스씨":"01085038","브이티":"01759581",
  "코리아나":"00118166","연우":"00372253","인터로조":"00604426",
  "한국화장품":"00823429","애경산업":"00139454","제닉":"00624998",
  "넷마블":"00904672","크래프톤":"00760971","카카오게임즈":"01137383",
  "펄어비스":"01152470","컴투스":"00476498","위메이드":"00444329",
  "데브시스터즈":"01008762","더블유게임즈":"01010110","엠게임":"00397058",
  "액토즈소프트":"00348034","조이시티":"00397252","네오위즈":"00628860",
  "웹젠":"00405320","드래곤플라이":"00230036","NHN":"00983271",
  "컴투스홀딩스":"00535746",
};

function findCode(name) {
  if (CODES[name]) return [CODES[name], name];
  const cands = Object.keys(CODES).filter(k => k.includes(name) || name.includes(k));
  if (cands.length) {
    cands.sort((a,b) => Math.abs(a.length-name.length) - Math.abs(b.length-name.length));
    return [CODES[cands[0]], cands[0]];
  }
  return [null, null];
}

function avCode(text, ac) {
  const m = text.match(new RegExp(`<T[EUH][^>]+(?:ACODE|AUNIT)="${ac}"[^>]*>([\\s\\S]*?)</T[EUH]>`, "i"));
  if (!m) return null;
  return m[1].replace(/<[^>]+>/g,"").replace(/\s+/g," ").trim();
}

function toInt(s) {
  if (!s) return null;
  const n = parseInt(s.replace(/[^\d]/g,""));
  return isNaN(n) ? null : n;
}

function parseAudit(text) {
  const res = {auditor:null,fc:null,hc:null,fa:null,ha:null,opinion:null};

  for (const ac of ["OPN_AUR1_A","OPN_AUR1_C","SIGK_AUR1"]) {
    const v = avCode(text, ac);
    if (v && v.includes("회계법인")) {
      res.auditor = v.replace(/\(주\d+\)/g,"").trim(); break;
    }
  }

  const OP = {"1":"적정","2":"한정","3":"부적정","4":"의견거절"};
  for (const ac of ["OPN_CMT1_A","OPN_CMT1_C","OPN_CMT2_A"]) {
    const v = avCode(text, ac);
    if (!v) continue;
    const m2 = text.match(new RegExp(`AUNIT="${ac}"[^>]*AUNITVALUE="([^"]*)"`, "i"));
    if (m2 && OP[m2[1]]) { res.opinion = OP[m2[1]]; break; }
    if (v.includes("적정") && !v.includes("부") && !v.includes("한")) { res.opinion="적정"; break; }
    if (v.includes("한정")) { res.opinion="한정"; break; }
    if (v.includes("부적정")) { res.opinion="부적정"; break; }
    if (v.includes("거절")) { res.opinion="의견거절"; break; }
  }

  res.fc = toInt(avCode(text,"SIGK_CPAY1"));
  res.hc = toInt(avCode(text,"SIGK_CTIM1"));
  res.fa = toInt(avCode(text,"SIGK_FPAY1"));
  res.ha = toInt(avCode(text,"SIGK_FTIM1"));

  for (const k of ["fc","fa"]) if (res[k] && !(1<=res[k] && res[k]<=200000)) res[k]=null;
  for (const k of ["hc","ha"]) if (res[k] && !(1<=res[k] && res[k]<=500000)) res[k]=null;

  if (!res.auditor) {
    const m = text.match(/([가-힣]+\s*(?:PwC|KPMG|EY|Deloitte)?\s*회계법인)/);
    if (m) res.auditor = m[1].trim();
  }
  return res;
}

// ZIP 파싱 (TextDecoder로 최대한 텍스트 추출)
async function extractZipText(buf) {
  const bytes = new Uint8Array(buf);
  // Local file header signature: PK\x03\x04
  const parts = [];
  let i = 0;
  while (i < bytes.length - 4) {
    if (bytes[i]===0x50 && bytes[i+1]===0x4B && bytes[i+2]===0x03 && bytes[i+3]===0x04) {
      const fnLen   = bytes[i+26] | (bytes[i+27]<<8);
      const extraLen= bytes[i+28] | (bytes[i+29]<<8);
      const compSize= bytes[i+18] | (bytes[i+19]<<8) | (bytes[i+20]<<16) | (bytes[i+21]<<24);
      const fnBytes = bytes.slice(i+30, i+30+fnLen);
      const fname   = new TextDecoder("utf-8",{fatal:false}).decode(fnBytes);
      const dataStart = i+30+fnLen+extraLen;
      const method  = bytes[i+8] | (bytes[i+9]<<8);
      if (/\.(xml|htm|html)$/i.test(fname) && compSize > 0) {
        const raw = bytes.slice(dataStart, dataStart+compSize);
        if (method === 0) { // stored (no compression)
          parts.push(new TextDecoder("utf-8",{fatal:false}).decode(raw));
        } else if (method === 8) { // deflate
          try {
            const ds = new DecompressionStream("deflate-raw");
            const writer = ds.writable.getWriter();
            writer.write(raw); writer.close();
            const out = await new Response(ds.readable).arrayBuffer();
            parts.push(new TextDecoder("utf-8",{fatal:false}).decode(out));
          } catch {
            // euc-kr fallback
            parts.push(new TextDecoder("euc-kr",{fatal:false}).decode(raw));
          }
        }
      }
      i = dataStart + Math.max(compSize, 1);
    } else {
      i++;
    }
  }
  return parts.join("\n");
}

const CORS = {
  "Access-Control-Allow-Origin":"*",
  "Access-Control-Allow-Methods":"POST,OPTIONS",
  "Access-Control-Allow-Headers":"Content-Type",
  "Content-Type":"application/json; charset=utf-8",
};

function ok(data)  { return new Response(JSON.stringify(data), {headers:CORS}); }
function err(corp_name, msg) { return ok({ok:false, corp_name, msg}); }

export default async function handler(req) {
  if (req.method==="OPTIONS") return new Response(null,{headers:CORS});

  const {corp_name, sector, year="2026", rcode="A003", ftype="계약"} = await req.json();

  const [code, matched] = findCode(corp_name);
  if (!code) return err(corp_name, "기업코드 미발견");

  // 공시 목록
  const listRes = await fetch(
    `${BASE}/list.json?crtfc_key=${KEY}&corp_code=${code}&bgn_de=${year}0101&end_de=${year}1231&pblntf_detail_ty=${rcode}&page_count=10`
  );
  const listData = await listRes.json();
  if (listData.status!=="000" || !listData.list?.length)
    return err(matched, `${year}년 ${REPRT[rcode]||rcode} 공시 없음`);

  const items = listData.list;
  const final = items.filter(i=>!i.rm).length ? items.filter(i=>!i.rm) : items;
  const {rcept_no, corp_name:realName} = final[0];

  // 원문 다운로드 + 파싱
  const docRes = await fetch(`${BASE}/document.xml?crtfc_key=${KEY}&rcept_no=${rcept_no}`);
  const docBuf = await docRes.arrayBuffer();
  const text   = await extractZipText(docBuf);

  const info    = parseAudit(text);
  const fee     = ftype==="계약" ? info.fc : info.fa;
  const hours   = ftype==="계약" ? info.hc : info.ha;
  const fph     = (fee&&hours) ? Math.round(fee*1000000/hours) : null;
  const auditor = info.auditor || "파싱실패";

  return ok({
    ok:true, sector, corp_name:realName||matched,
    auditor, is_big4: BIG4.some(b=>auditor.includes(b)),
    fee, hours, fph,
    opinion: info.opinion||"—", period:"—",
    fee_contract:info.fc, hours_contract:info.hc,
    fee_actual:info.fa,   hours_actual:info.ha,
    rcept_no,
  });
}
