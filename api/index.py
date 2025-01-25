from flask import Flask, request, send_from_directory
import os

app = Flask(__name__)

# Serve static files from the root directory
@app.route('/')
def serve_index():
    return send_from_directory('../templates', 'index.html')

# Serve other static files
@app.route('/<path:path>')
def serve_static(path):
    if path.startswith('static/'):
        return send_from_directory('..', path)
    elif os.path.exists(f'../templates/{path}.html'):
        return send_from_directory('../templates', f'{path}.html')
    return send_from_directory('../templates', 'index.html')

# Add your API routes here
@app.route('/api/articles')
def api_articles():
    # Your existing api_articles code here
    pass

if __name__ == '__main__':
    app.run() 