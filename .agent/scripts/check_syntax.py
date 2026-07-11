import ast, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for f in ['app/gemini_session.py', 'app/broadcast.py', 'app/server.py']:
    f = os.path.join(ROOT, f)
    try:
        ast.parse(open(f).read())
        print('OK', f)
    except SyntaxError as e:
        print('ERR', f, e)
        sys.exit(1)
print('All OK')
