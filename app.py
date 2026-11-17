import os, json, urllib.request, urllib.error
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

try:
    CHANNELS = json.loads(os.environ.get("GATEWAY_CHANNELS", "[]"))
except Exception:
    CHANNELS = []
MASTER_KEY = os.environ.get("GATEWAY_KEY", "")


def authorized():
    if not MASTER_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == "Bearer " + MASTER_KEY or request.headers.get("x-api-key") == MASTER_KEY


def call_upstream(channel, payload):
    url = channel["base_url"].rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + channel["api_key"])
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) api-gateway")
    return urllib.request.urlopen(req, timeout=120)


@app.get("/")
def root():
    return jsonify({"status": "ok", "channels": len(CHANNELS)})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "channels": len(CHANNELS)})


@app.get("/v1/models")
def models():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    seen, data = set(), []
    for c in CHANNELS:
        for m in c.get("models", []):
            if m not in seen:
                seen.add(m)
                data.append({"id": m, "object": "model", "owned_by": c["name"]})
    return jsonify({"object": "list", "data": data})


@app.post("/v1/chat/completions")
def chat():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not CHANNELS:
        return jsonify({"error": "no channels configured"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    model = payload.get("model", "")
    stream = bool(payload.get("stream"))
    forced = request.headers.get("x-force-channel", "")
    if forced:
        targets = [c for c in CHANNELS if c["name"] == forced]
        if not targets:
            return jsonify({"error": "unknown channel"}), 400
    else:
        targets = [c for c in CHANNELS if model in c.get("models", [])] or list(CHANNELS)
    last_err = None
    for c in targets:
        try:
            resp = call_upstream(c, payload)
            if stream:
                def gen():
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        yield chunk
                r = Response(gen(), status=200, content_type="text/event-stream")
                r.headers["x-gateway-channel"] = c["name"]
                return r
            body = resp.read()
            r = Response(body, status=200, content_type="application/json")
            r.headers["x-gateway-channel"] = c["name"]
            return r
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (401, 403):
                return Response(e.read(), status=e.code, content_type="application/json")
            continue
        except Exception as e:
            last_err = e
            continue
    return jsonify({"error": "all channels failed", "detail": str(last_err)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
