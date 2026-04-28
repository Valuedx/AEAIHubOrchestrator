import urllib.request

endpoints = ['/mcp', '/stream', '/streamable', '/mcp/stream', '/streamable-http', '/mcp/', '/messages', '/sse', '/']
for ep in endpoints:
    try:
        req = urllib.request.Request('http://127.0.0.1:3000' + ep, method='POST')
        res = urllib.request.urlopen(req)
        print('SUCCESS:', ep, res.status)
    except Exception as e:
        print('FAIL:', ep, getattr(e, 'code', str(e)))
