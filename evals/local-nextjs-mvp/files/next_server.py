"""Simulates a locally running Next.js MVP (Tasklane) on localhost:3000. Dashboard needs cookie session=test."""
from http.server import BaseHTTPRequestHandler, HTTPServer
NAV_PUB = '<header><nav><a href="/">Tasklane</a><a href="/pricing">Pricing</a><a href="/login">Log in</a><a href="/signup">Sign up</a></nav></header>'
NAV_APP = '<header><nav><a href="/dashboard">Dashboard</a><a href="/dashboard/projects">Projects</a><a href="/dashboard/settings">Settings</a><a href="/logout">Log out</a></nav></header>'
FOOT = '<footer><a href="/pricing">Pricing</a><a href="/privacy">Privacy</a></footer>'
def doc(title, nav, body): return f'<!DOCTYPE html><html><head><title>{title} | Tasklane</title></head><body>{nav}<main>{body}</main>{FOOT}</body></html>'
PUB = {
 "/": ("Tasklane", "<h1>Plan work in lanes</h1><p>Tasklane helps small teams plan tasks in swimlanes.</p><a href='/signup'>Start free</a><a href='/pricing'>See pricing</a>"),
 "/pricing": ("Pricing", "<h1>Pricing</h1><p>Free and Team plans.</p><a href='/signup'>Start free</a><a href='/contact-sales'>Talk to sales</a>"),
 "/login": ("Log in", "<h1>Log in</h1><form method='post'><input name='email'><input name='password' type='password'></form><a href='/signup'>Create an account</a>"),
 "/signup": ("Sign up", "<h1>Create your account</h1><form method='post'><input name='name'><input name='email'><input name='password' type='password'></form><a href='/login'>Have an account?</a><a href='/privacy'>Privacy</a>"),
}
APP = {
 "/dashboard": ("Dashboard", "<h1>Your week</h1><p>Tasks due this week across projects.</p><a href='/dashboard/projects'>All projects</a><a href='/dashboard/projects/1'>Website relaunch</a>"),
 "/dashboard/projects": ("Projects", "<h1>Projects</h1>" + "".join(f"<a href='/dashboard/projects/{i}'>Project {i}</a>" for i in range(1,4)) + "<a href='#'>New project</a>"),
 "/dashboard/settings": ("Settings", "<h1>Settings</h1><form><input name='name'><input name='email'></form>"),
}
for i in range(1,4): APP[f"/dashboard/projects/{i}"] = (f"Project {i}", f"<h1>Project {i}</h1><p>Lanes: To do, Doing, Done.</p><a href='/dashboard/projects'>Back to projects</a>")
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self, code, body="", loc=None):
        self.send_response(code)
        if loc: self.send_header("Location", loc)
        self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        authed = "session=test" in (self.headers.get("Cookie") or "")
        if p in PUB: t,b = PUB[p]; return self.send(200, doc(t, NAV_PUB, b))
        if p in APP:
            if not authed: return self.send(307, "", "/login?next="+p)
            t,b = APP[p]; return self.send(200, doc(t, NAV_APP, b))
        self.send(404, doc("Not found", NAV_PUB, "<h1>404</h1>"))
HTTPServer(("127.0.0.1",3000),H).serve_forever()
