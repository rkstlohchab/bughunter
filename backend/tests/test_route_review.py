"""Route-by-route source review: discovery across frameworks and per-route flags."""
from __future__ import annotations

import pytest

from bughunter.app import Application
from bughunter.route_review.routes import analyze

FILES = {
    "server/routes/orders.js": """
const express = require('express');
const router = express.Router();
app.get('env');
router.get('/api/orders/:id', async (req, res) => {
  const order = await Order.findById(req.params.id);
  res.json(order);
});
router.get('/api/me/orders/:id', requireAuth, async (req, res) => {
  const order = await Order.findOne({ _id: req.params.id, userId: req.user.id });
  res.json(order);
});
router.post('/api/report', requireAuth, async (req, res) => {
  const rows = await db.query(`SELECT * FROM reports WHERE name = '${req.body.name}'`);
  res.json(rows);
});
router.post('/api/fetch', requireAuth, async (req, res) => {
  const page = await axios.get(req.body.url);
  res.send(page.data);
});
router.post('/api/profile', requireAuth, async (req, res) => {
  const body = ProfileSchema.parse(req.body);
  await User.update(body, { where: { id: req.user.id } });
  res.json({ ok: true });
});
router.post('/api/login', async (req, res) => { res.json(await login(req.body)); });
router.get('/health', (req, res) => res.send('ok'));
module.exports = router;
""",
    "app/api/invoices/[id]/route.ts": """
import { getServerSession } from 'next-auth';
export async function GET(request, { params }) {
  const session = await getServerSession();
  const invoice = await prisma.invoice.findFirst({ where: { id: params.id, userId: session.user.id } });
  return Response.json(invoice);
}
export async function DELETE(request, { params }) {
  await prisma.invoice.delete({ where: { id: params.id } });
  return new Response(null, { status: 204 });
}
""",
    "backend/main.py": """
from fastapi import FastAPI, Depends
app = FastAPI()

@app.get("/files/{name}")
def read_file(name: str):
    return open(os.path.join("/srv/files", request.path_params["name"])).read()

@app.post("/admin/run")
def run(cmd: str, user=Depends(get_current_user)):
    return subprocess.run(request.json["cmd"], shell=True)
""",
    "web/views.py": """
@bp.route("/go")
def go():
    return redirect(request.args.get("next"))
""",
    "shop/urls.py": """
urlpatterns = [path('cart/<int:item_id>/', views.cart_item)]
""",
    "config/routes.rb": "  post '/payments/refund', to: 'payments#refund'\n",
    "routes/web.php": "<?php Route::post('/upload', [UploadController::class, 'store']);\n",
    "src/UserController.java": """
@RestController
@RequestMapping("/api/users")
public class UserController {
  @GetMapping("/{id}")
  public User get(@PathVariable Long id) { return repo.findById(id); }
}
""",
    "cmd/server.go": 'func main() { r := gin.Default(); r.POST("/api/transfer", transfer) }\n',
    "src/config.ts": "export const cors = { origin: '*', credentials: true };\nconst key = 'AKIA" + "ABCDEFGHIJKLMNOP';\n",
    "node_modules/lib/index.js": "router.get('/should/not/appear', h);\n",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "site"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _flags(result, method, path):
    route = next(r for r in result["routes"] if r.method == method and r.path == path)
    return {f["kind"] for f in route.flags}


def test_discovers_routes_across_frameworks(repo):
    result = analyze(repo)
    found = {(r.method, r.path, r.framework) for r in result["routes"]}
    for expected in [("GET", "/api/orders/:id", "express"), ("GET", "/api/invoices/:id", "nextjs_app_router"),
                     ("DELETE", "/api/invoices/:id", "nextjs_app_router"), ("GET", "/files/{name}", "flask_fastapi"),
                     ("GET", "/go", "flask_fastapi"), ("ANY", "/cart/<int:item_id>/", "django"),
                     ("POST", "/payments/refund", "rails"), ("POST", "/upload", "laravel"),
                     ("GET", "/api/users/{id}", "spring"), ("POST", "/api/transfer", "go")]:
        assert expected in found, expected
    assert not any(r.path == "/should/not/appear" or r.path == "env" for r in result["routes"])
    assert not any(r.framework == "spring" and r.path == "/api/users" for r in result["routes"])


def test_flags_vulnerable_handlers_and_spares_safe_ones(repo):
    result = analyze(repo)
    assert {"no_auth_check", "missing_ownership_check"} <= _flags(result, "GET", "/api/orders/:id")
    assert _flags(result, "GET", "/api/me/orders/:id") == set()
    assert "sql_built_from_input" in _flags(result, "POST", "/api/report")
    assert "server_side_fetch" in _flags(result, "POST", "/api/fetch")
    assert _flags(result, "POST", "/api/profile") == set()
    assert "no_rate_limit_on_auth" in _flags(result, "POST", "/api/login")
    assert _flags(result, "GET", "/health") == set()
    assert _flags(result, "GET", "/api/invoices/:id") == set()
    assert {"no_auth_check", "missing_ownership_check"} <= _flags(result, "DELETE", "/api/invoices/:id")
    assert "path_from_input" in _flags(result, "GET", "/files/{name}")
    assert "command_execution" in _flags(result, "POST", "/admin/run")
    assert "open_redirect" in _flags(result, "GET", "/go")
    file_kinds = {f["kind"] for f in result["file_flags"]}
    assert {"cors_wildcard_with_credentials", "hardcoded_secret"} <= file_kinds
    location = next(f for f in result["file_flags"] if f["kind"] == "hardcoded_secret")["location"]
    assert location == "src/config.ts:2"
    sql = next(f for r in result["routes"] for f in r.flags if f["kind"] == "sql_built_from_input")
    assert sql["location"].startswith("server/routes/orders.js:") and sql["tbhm_section"] == "sqli" and sql["live_check"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = Application(tmp_path / "data", primary=False)
    monkeypatch.setattr(a.operator, "_loop", lambda *args: None)
    a.store.put("project", {"name": "Live site", "scope": ["https://shop.example.test/"], "authorization": "Owner"}, id="proj_live")
    yield a
    a.close()


def test_service_and_tool(app, repo):
    reviews = app.route_review
    with pytest.raises(ValueError):
        reviews.review("proj_live", "https://example.com/not-a-repo")
    with pytest.raises(ValueError):
        reviews.review("proj_live", str(repo / "missing"))
    summary = reviews.review("proj_live", str(repo))
    assert summary["stats"]["routes"] >= 14 and summary["by_severity"]["critical"] >= 1
    assert summary["riskiest_routes"][0]["flags"]
    high = reviews.routes(summary["review_id"], "high", path_contains="orders")
    assert high["routes"] and all(r["path"].find("orders") >= 0 for r in high["routes"])
    from bughunter.operator_tools import OperatorTools
    chat = app.store.put("chat", {"title": "Live", "project_id": "proj_live", "messages": []})
    task = app.harness.start("Review my website code for vulnerable routes", grants=["app"], id=chat["id"], web_search_mode="off")
    tools = OperatorTools(app, task["run_id"])
    assert tools.route_review("latest")["review_id"] == summary["review_id"]
    assert tools.route_review("routes", review_id=summary["review_id"], kind="command_execution")["total"] == 1
    plan_chat = app.store.put("chat", {"title": "Plan", "project_id": "proj_live", "messages": []})
    plan = app.harness.start("Review my website code", grants=["app"], id=plan_chat["id"], execution_mode="plan", web_search_mode="off")
    with pytest.raises(PermissionError, match="Plan mode"):
        OperatorTools(app, plan["run_id"]).route_review("review", source=str(repo))
