import json, os, urllib.request, urllib.error
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

def load_channels():
    raw = os.environ.get("GATEWAY_CHANNELS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def master_key_ok():
    key = os.environ.get("GATEWAY_KEY", "").strip()
    if not key:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == "Bearer " + key

@app.get("/")
def index():
    return "ok"

@app.get("/health")
def health():
    return jsonify(status="ok", channels=len(load_channels()))

@app.get("/v1/models")
def models():
    if not master_key_ok():
        return jsonify(error="unauthorized"), 401
    seen = []
    for ch in load_channels():
        for m in ch.get("models", []):
            if m not in seen:
                seen.append(m)
    return jsonify(object="list", data=[{"id": m, "object": "model"} for m in seen])

@app.post("/v1/chat/completions")
def chat():
    if not master_key_ok():
        return jsonify(error="unauthorized"), 401
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model", "")
    channels = [c for c in load_channels() if model in c.get("models", [])] or load_channels()
    if not channels:
        return jsonify(error="no channels configured"), 503
    stream = bool(body.get("stream"))
    last_err = None
    for ch in channels:
        url = ch["base_url"].rstrip("/") + "/chat/completions"
        payload = json.dumps(body).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + ch.get("api_key", ""))
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                try:
                    data = e.read()
                except Exception:
                    data = b""
                return Response(data, status=e.code, content_type=e.headers.get("Content-Type", "application/json"))
            last_err = "upstream %s: HTTP %s" % (ch.get("name", "?"), e.code)
            continue
        except Exception as e:
            last_err = "upstream %s: %s" % (ch.get("name", "?"), e)
            continue
        if stream:
            def gen(r=resp):
                while True:
                    chunk = r.read(4096)
                    if not chunk:
                        break
                    yield chunk
            return Response(gen(), status=resp.status, content_type=resp.headers.get("Content-Type", "text/event-stream"))
        data = resp.read()
        return Response(data, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))
    return jsonify(error="all upstreams failed", detail=str(last_err)), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
