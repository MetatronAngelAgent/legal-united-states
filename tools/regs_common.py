"""
regs_common.py — shared helpers for the state-regulation converters (la_lac, mo_csr, ri_ricr, ...).

Each converter yields Section records; this module writes them as OKF v0.2 concepts under
<out>/<state>/regulations/..., builds index.md files, and writes a stats report.
"""
import datetime as dt, hashlib, json, os, re, sys, time, urllib.request
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
_last = [0.0]
_robots = {}

def allowed(url):
    """robots.txt check (cached per host). Missing/unreadable robots.txt = allowed."""
    import urllib.robotparser, urllib.parse
    u = urllib.parse.urlsplit(url)
    host = f"{u.scheme}://{u.netloc}"
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            if "<html" in body[:500].lower():
                body = ""                       # SPA returning its index page for every path
            rp.parse(body.splitlines())
        except Exception:
            rp.parse([])
        _robots[host] = rp
    return _robots[host].can_fetch("OKF-LawCorpus", url)

def fetch(url, dest, delay=2.0, tries=4, timeout=300):
    """Cache-first polite download. Returns dest, or None on a 4xx."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    if not allowed(url):
        print(f"  robots.txt disallows: {url}", flush=True)
        return None
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    for a in range(tries):
        wait = delay - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(dest + ".part", "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            os.replace(dest + ".part", dest)
            return dest
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                print(f"  HTTP {e.code}: {url}", flush=True)
                return None
            time.sleep(10 * (a + 1))
        except Exception as e:
            if a == tries - 1:
                print(f"  failed: {url}: {e}", flush=True)
                return None
            time.sleep(10 * (a + 1))

class Writer:
    def __init__(self, out, state, label, tool, source_id, source_title, source_url, license, edition, official=True):
        self.out, self.state, self.label, self.tool = out, state, label, tool
        self.root = os.path.join(out, state, "regulations")
        self.src = (source_id, source_title, source_url)
        self.license, self.edition, self.official = license, edition, official
        self.run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.idx, self.names, self.used = defaultdict(list), {}, set()
        self.n, self.deprecated, self.empty = 0, 0, 0

    def name_dir(self, d, name):
        self.names[d] = name

    def write(self, dirs, fname, citation, heading, body, resource, hierarchy, history="", notes="",
              extra=None, tags=None, split_chars=6000):
        ddir = os.path.join(self.root, *dirs)
        path = os.path.join(ddir, fname + ".md")
        k = 2
        while path in self.used:
            path = os.path.join(ddir, f"{fname}-v{k}.md"); k += 1
        self.used.add(path)
        body = body.strip()
        repealed = bool(re.search(r"\b(Repealed|Reserved|Rescinded|Expired)\b", heading)) and len(body) < 400
        if not body:
            self.empty += 1
            body = "(No rule text in this source" + ("; see heading." if repealed else ".") + ")"
        if repealed:
            self.deprecated += 1
        heading = U.normspace(heading).rstrip(".") or "(untitled)"
        title = f"{citation} — {heading}" if heading and heading not in citation else citation
        pairs = [
            ("type", "Regulation Section"), ("title", title[:300]), ("description", heading[:240]),
            ("resource", resource),
            ("tags", [self.state, "regulations", "state-law"] + (tags or [])),
            ("citation", citation), ("jurisdiction", self.state), ("jurisdiction_level", "state"),
            ("hierarchy", hierarchy), ("edition", self.edition),
            ("official_status", "official publication" if self.official else "unofficial online version"),
            ("status", "deprecated" if repealed else "stable"),
            ("license", self.license),
        ] + list((extra or {}).items()) + [
            ("generated", {"by": self.tool, "at": self.run_at}),
            ("sources", ("raw", "\n  - { id: " + self.src[0] + ", resource: " + U.yq(self.src[2]) +
                         ", title: " + U.yq(self.src[1]) + ", author: " + U.yq("process:" + self.src[0]) + " }")),
            ("checksum", hashlib.sha256(body.encode()).hexdigest()),
        ]
        md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n"
        if history.strip():
            md += "\n# History\n\n" + history.strip() + "\n"
        if notes.strip():
            md += "\n# Notes\n\n" + notes.strip() + "\n"
        os.makedirs(ddir, exist_ok=True)
        if len(body) > split_chars * 4:
            # very long rule (big tables, fee schedules): parent + parts
            paras = body.split("\n\n")
            chunks, cur, sz = [], [], 0
            for p in paras:
                if cur and sz + len(p) > split_chars:
                    chunks.append(cur); cur, sz = [], 0
                cur.append(p); sz += len(p) + 2
            if cur:
                chunks.append(cur)
            pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
            links = []
            for i, c in enumerate(chunks, 1):
                pb = "\n\n".join(c); pt = f"{title[:250]} (part {i} of {len(chunks)})"
                pp = [(x, pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v)
                      for x, v in pairs] + [("part_of", f"{i} of {len(chunks)}")]
                open(os.path.join(pdir, f"part-{i:03d}.md"), "w", encoding="utf-8").write(
                    U.emit_frontmatter(pp) + "\n# Text\n\n" + pb + "\n")
                links.append((f"{os.path.basename(pdir)}/part-{i:03d}.md", pt))
            md = (U.emit_frontmatter(pairs + [("parts", ("raw", str(len(chunks))))]) + "\n# Text\n\n" + paras[0] +
                  "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in links) + "\n" +
                  (("\n# History\n\n" + history.strip() + "\n") if history.strip() else ""))
        open(path, "w", encoding="utf-8").write(md)
        self.idx[ddir].append((os.path.basename(path), title))
        self.n += 1
        return path

    def finish(self, intro, stats):
        def nk(s):
            return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]
        alld = set(self.idx)
        for d in list(alld):
            while len(d) > len(self.root):
                d = os.path.dirname(d); alld.add(d)
        alld.add(self.root)
        for d in sorted(alld):
            subs = sorted((x for x in os.listdir(d) if os.path.join(d, x) in alld), key=nk)
            items = sorted(self.idx.get(d, []), key=lambda t: nk(t[0]))
            nm = self.names.get(d) or os.path.relpath(d, os.path.join(self.out, self.state)).replace(os.sep, " / ")
            head = f"# {self.label} — {nm}\n\n" + (intro + "\n\n" if d == self.root else "")
            open(os.path.join(d, "index.md"), "w", encoding="utf-8").write(
                head + "\n".join([f"* [{self.names.get(os.path.join(d, s), s)}]({s}/)" for s in subs] +
                                 [f"* [{t}]({f})" for f, t in items]) + "\n")
        sdir = os.path.join(self.out, self.state)
        kinds = sorted(x for x in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, x)))
        open(os.path.join(sdir, "index.md"), "w", encoding="utf-8").write(
            f"# {self.label}\n\n" + "\n".join(f"* [{k.replace('-', ' ').title()}]({k}/)" for k in kinds) + "\n")
        ref = os.path.join(self.out, "references"); os.makedirs(ref, exist_ok=True)
        stats = dict(stats, written=self.n, deprecated=self.deprecated, empty=self.empty)
        json.dump(stats, open(os.path.join(ref, f"ingest-stats-regs-{self.state}.json"), "w"), indent=1)
        print(f"{self.state}: {self.n} regulation concepts ({self.deprecated} repealed/reserved, {self.empty} empty)", flush=True)
        return stats
