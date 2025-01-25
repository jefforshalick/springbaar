import os
import sqlite3
import threading
import time
import logging
import urllib.parse
from datetime import datetime, timedelta
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import feedparser
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect
from werkzeug.local import LocalProxy
from flask_caching import Cache
from dateutil import parser
import re
import base64
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change from DEBUG to INFO
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Only log errors for urllib3 and werkzeug
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Configure Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# RSS feed URLs with specific handling rules
FEEDS = {
    'Hodinkee': {
        'url': 'https://www.hodinkee.com/feed',
        'image_selector': 'img.article-hero'
    },
    'Worn & Wound': {
        'url': 'https://wornandwound.com/feed/',
        'image_selector': '.post-header img.attachment-post-thumbnail, .post-content img:first-of-type'
    },
    'ABTW': {
        'url': 'https://www.ablogtowatch.com/feed/',
        'image_selector': '.entry-content img:first-of-type, .featured-image img, meta[property="og:image"]'
    },
    'Fratello': {
        'url': 'https://www.fratellowatches.com/feed/',
        'image_selector': 'meta[property="og:image"]'
    },
    'Monochrome': {
        'url': 'https://monochrome-watches.com/feed/',
        'image_selector': 'meta[property="og:image"]'
    },
    'Watchtime': {
        'url': 'https://www.watchtime.com/feed/',
        'image_selector': 'meta[property="og:image"]'
    },
    'Time+Tide': {
        'url': 'https://timeandtidewatches.com/feed',
        'image_selector': 'meta[property="og:image"]'
    },
    'Windup Watch Shop': {
        'url': 'https://windupwatchshop.com/blogs/chronicle/feed',
        'image_selector': '.article-featured-image img, .article__image-wrapper img'
    }
}

def process_image_url(image_url, source):
    """Process and format image URLs based on source."""
    if not image_url:
        return None
        
    # Clean up the URL
    image_url = image_url.strip()
    
    # Handle protocol-relative URLs
    if image_url.startswith('//'):
        image_url = 'https:' + image_url
        
    # Handle source-specific processing
    if source == 'Fratello':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://www.fratellowatches.com/' + image_url.lstrip('/')
        # Add CDN optimization for Fratello images
        if '/wp-content/' in image_url and '/cdn-cgi/image/' not in image_url:
            image_url = image_url.replace('/wp-content/', '/cdn-cgi/image/format=auto,quality=85/wp-content/')
    
    elif source == 'ABTW':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://www.ablogtowatch.com/' + image_url.lstrip('/')
            
    elif source == 'Worn & Wound':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://wornandwound.com/' + image_url.lstrip('/')
    
    elif source == 'Time+Tide':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://timeandtidewatches.com/' + image_url.lstrip('/')
        # Add CDN optimization for Time+Tide images
        if '/wp-content/' in image_url and '/cdn-cgi/image/' not in image_url:
            image_url = image_url.replace('/wp-content/', '/cdn-cgi/image/format=auto,quality=85/wp-content/')
    
    elif source == 'Monochrome':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://monochrome-watches.com/' + image_url.lstrip('/')
            
    elif source == 'Windup Watch Shop':
        if not image_url.startswith(('http://', 'https://')):
            image_url = 'https://windupwatchshop.com/' + image_url.lstrip('/')
        # Handle Shopify CDN URLs
        if '//cdn.shopify.com/' in image_url:
            # Ensure we're using the highest quality version
            image_url = re.sub(r'_\d+x\d+\.', '.', image_url)
            image_url = re.sub(r'\?v=\d+', '', image_url)
    
    # Ensure URL is absolute
    if not image_url.startswith(('http://', 'https://')):
        image_url = 'https://' + image_url.lstrip('/')
    
    return image_url

def fetch_article_image(url, selector=None):
    """Fetch image URL from an article page."""
    try:
        session = requests.Session()
        response = session.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        })
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # First try og:image meta tag
        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
        if og_image and og_image.get('content'):
            return ensure_absolute_url(og_image.get('content'), url)
        
        # Then try twitter:image
        twitter_image = soup.find('meta', property='twitter:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            return ensure_absolute_url(twitter_image.get('content'), url)
        
        # Try featured image
        featured_img = soup.find('img', class_=lambda x: x and ('featured' in x.lower() or 'hero' in x.lower()))
        if featured_img and featured_img.get('src'):
            return ensure_absolute_url(featured_img.get('src'), url)
        
        # Try first large image
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and not any(x in src.lower() for x in ['avatar', 'logo', 'icon', 'banner', 'ad-']):
                return ensure_absolute_url(src, url)
        
        return None
        
    except Exception as e:
        logger.error(f"[FETCH] Error fetching article image from {url}: {str(e)}")
        return None

def validate_image_url(url):
    """Validate that an image URL exists and returns a valid image."""
    try:
        # Only validate URLs from certain domains that we know are problematic
        if not any(domain in url.lower() for domain in ['ablogtowatch.com', 'watchtime.com', 'fratellowatches.com']):
            return True
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'image/*',
        }
        
        # Use HEAD request instead of GET for faster validation
        response = requests.head(url, timeout=5, headers=headers, allow_redirects=True)
        content_type = response.headers.get('content-type', '').lower()
        
        return response.status_code == 200 and ('image' in content_type or content_type.endswith('/webp'))
        
    except Exception as e:
        return False

def ensure_absolute_url(url, base_url):
    """Ensure a URL is absolute by combining it with a base URL if necessary."""
    if not url:
        return None
    
    # Clean up the URL
    url = url.strip()
    
    # If it's already an absolute URL, return it
    if url.startswith(('http://', 'https://')):
        return url
    
    # If it's a protocol-relative URL, add https:
    if url.startswith('//'):
        return 'https:' + url
    
    try:
        # Parse the base URL to get the domain
        parsed_base = urllib.parse.urlparse(base_url)
        base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"
        
        # If it starts with /, join it with the base domain
        if url.startswith('/'):
            return urllib.parse.urljoin(base_domain, url)
        
        # Otherwise, join it with the base URL
        return urllib.parse.urljoin(base_url, url)
    except Exception as e:
        logger.error(f"[URL] Error making URL absolute: {str(e)}")
        return None

def extract_image_from_content(content):
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    
    # Look for high-res images first
    for img in soup.find_all('img'):
        src = img.get('src', '')
        # Skip advertisement images
        if any(x in src.lower() for x in ['banner', 'ad-', 'advertisement']):
            continue
        if any(x in src.lower() for x in ['large', 'full', 'original', 'hero', 'featured', 'wp-content/uploads']):
            return src
    
    # Then try data-src attribute (common for lazy-loaded images)
    for img in soup.find_all('img'):
        src = img.get('data-src') or img.get('data-lazy-src')
        if src and not any(x in src.lower() for x in ['banner', 'ad-', 'advertisement']):
            return src
    
    # Finally, just get the first non-advertisement image
    for img in soup.find_all('img'):
        src = img.get('src')
        if src and not any(x in src.lower() for x in ['banner', 'ad-', 'advertisement']):
            return src
    return None

def clean_html_content(content):
    if not content:
        return ''
    soup = BeautifulSoup(content, 'html.parser')
    # Remove all images from the summary
    for img in soup.find_all('img'):
        img.decompose()
    # Get clean text
    text = soup.get_text(strip=True)
    # Limit to ~200 characters
    return text[:200] + '...' if len(text) > 200 else text

def parse_date(date_str):
    """Parse a date string into a datetime object, handling multiple formats."""
    if not date_str:
        return datetime.now()
    
    try:
        if isinstance(date_str, datetime):
            return date_str
            
        if isinstance(date_str, str):
            # Try parsing ISO format with timezone
            try:
                from dateutil import parser as date_parser
                return date_parser.parse(date_str)
            except:
                pass
            
            # Try email format
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(date_str)
            except:
                pass
            
            # Try common formats without timezone
            formats = [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%a, %d %b %Y %H:%M:%S'
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue
                    
    except Exception as e:
        # Instead of logging an error, just return current time
        return datetime.now()
    
    return datetime.now()

@cache.memoize(timeout=1800)  # Cache for 30 minutes
def fetch_feed(source, feed_config):
    try:
        feed_url = feed_config['url']
        image_selector = feed_config.get('image_selector')
        
        # Get most recent article date for this source
        conn = sqlite3.connect('articles.db')
        c = conn.cursor()
        c.execute('SELECT published FROM articles WHERE source = ? ORDER BY published DESC LIMIT 1', (source,))
        row = c.fetchone()
        conn.close()
        
        most_recent_date = parse_date(row[0]) if row else None
        
        session = requests.Session()
        response = session.get(feed_url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml'
        })
        response.raise_for_status()
        
        feed = feedparser.parse(response.text)
        entries = []
        
        # Filter entries newer than most recent date
        entries_to_process = []
        for entry in feed.entries[:50]:  # Still check up to 50 entries
            entry_date = parse_date(entry.published if hasattr(entry, 'published') else None)
            if not most_recent_date or (entry_date and entry_date > most_recent_date):
                entries_to_process.append(entry)
        
        if not entries_to_process:
            logger.info(f"No new entries to process for {source}")
            return []
            
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_feed_entry, entry, source, image_selector) 
                      for entry in entries_to_process]
            entries = [result for future in futures 
                      if (result := future.result()) is not None]
        
        logger.info(f"Successfully processed {len(entries)} new entries from {source}")
        return entries
    except Exception as e:
        logger.error(f"Error fetching feed {source}: {str(e)}")
        return []

def process_feed_entry(entry, source, image_selector):
    """Process a single feed entry in parallel."""
    try:
        published = parse_date(entry.published if hasattr(entry, 'published') else None)
        
        # Extract tags
        tags = []
        if hasattr(entry, 'tags'):
            tags.extend(str(tag.term).strip() for tag in entry.tags if hasattr(tag, 'term') and tag.term)
            tags.extend(str(tag.label).strip() for tag in entry.tags if hasattr(tag, 'label') and tag.label)
        
        if hasattr(entry, 'category') and entry.category:
            tags.append(str(entry.category).strip())
        elif hasattr(entry, 'categories'):
            tags.extend(str(cat).strip() for cat in entry.categories if cat)
        
        # Clean up tags
        tags = list(set(tag for tag in tags if tag and len(tag) < 50))
        
        # Get image URL
        image_url = extract_image_from_entry(entry, image_selector)
        
        # Create entry dict
        feed_entry = {
            'title': entry.title if hasattr(entry, 'title') else '',
            'link': entry.link if hasattr(entry, 'link') else '',
            'published': published,
            'summary': clean_html_content(entry.summary if hasattr(entry, 'summary') else ''),
            'image_url': image_url,
            'source': source,
            'tags': tags
        }
        
        return feed_entry if feed_entry['title'] and feed_entry['link'] else None
        
    except Exception:
        return None

def extract_image_from_entry(entry, image_selector=None):
    """Extract image URL from a feed entry."""
    try:
        # Handle both attribute and dictionary access for title
        title = entry.title if hasattr(entry, 'title') else entry.get('title', 'Unknown title')
        
        # Get base URL - handle both attribute and dictionary access
        base_url = entry.link if hasattr(entry, 'link') else entry.get('link')
        if not base_url:
            return None
            
        # Try media:content first (often has highest quality images)
        media_content = getattr(entry, 'media_content', None) or entry.get('media_content')
        if media_content:
            if isinstance(media_content, list):
                for media in media_content:
                    url = media.get('url') if isinstance(media, dict) else getattr(media, 'url', None)
                    if url:
                        absolute_url = ensure_absolute_url(url, base_url)
                        if absolute_url:
                            return absolute_url
            elif isinstance(media_content, dict):
                url = media_content.get('url')
                if url:
                    absolute_url = ensure_absolute_url(url, base_url)
                    if absolute_url:
                        return absolute_url
        
        # Try media:thumbnail
        media_thumbnail = getattr(entry, 'media_thumbnail', None) or entry.get('media_thumbnail')
        if media_thumbnail:
            if isinstance(media_thumbnail, list):
                for thumbnail in media_thumbnail:
                    url = thumbnail.get('url') if isinstance(thumbnail, dict) else getattr(thumbnail, 'url', None)
                    if url:
                        absolute_url = ensure_absolute_url(url, base_url)
                        if absolute_url:
                            return absolute_url
            elif isinstance(media_thumbnail, dict):
                url = media_thumbnail.get('url')
                if url:
                    absolute_url = ensure_absolute_url(url, base_url)
                    if absolute_url:
                        return absolute_url
        
        # Try content
        content = None
        if hasattr(entry, 'content'):
            content = entry.content[0].value if isinstance(entry.content, list) else entry.content
        elif isinstance(entry, dict) and 'content' in entry:
            content = entry['content'][0].get('value') if isinstance(entry['content'], list) else entry['content']
            
        if content:
            soup = BeautifulSoup(content, 'html.parser')
            for img in soup.find_all('img'):
                src = img.get('src')
                if src and not any(x in src.lower() for x in ['avatar', 'logo', 'icon']):
                    absolute_url = ensure_absolute_url(src, base_url)
                    if absolute_url:
                        return absolute_url
        
        # Finally try article page
        image_url = fetch_article_image(base_url, image_selector)
        if image_url:
            absolute_url = ensure_absolute_url(image_url, base_url)
            if absolute_url:
                return absolute_url
        
        return None
        
    except Exception as e:
        logger.error(f"[IMAGE] Error extracting image from entry {title}: {str(e)}")
        return None

def get_articles(page=1, per_page=10, search='', source='', tag=''):
    offset = (page - 1) * per_page
    
    base_query = "SELECT * FROM articles"
    count_query = "SELECT COUNT(*) FROM articles"
    params = []
    
    conditions = []
    if search:
        conditions.append("(title LIKE ? OR summary LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%'])
    if source:
        conditions.append("source = ?")
        params.append(source)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%{tag}%')
    
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
        base_query += where_clause
        count_query += where_clause
    
    base_query += " ORDER BY published DESC LIMIT ? OFFSET ?"
    query_params = params.copy()
    query_params.extend([per_page, offset])
    
    with sqlite3.connect('articles.db') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get total count
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Get paginated articles
        cursor.execute(base_query, query_params)
        rows = cursor.fetchall()
        
        articles = []
        for row in rows:
            article = dict(row)
            article['published_date'] = datetime.fromisoformat(article['published']).strftime('%B %d, %Y')
            articles.append(article)
        
        return articles, total_count

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    articles, total_count = get_articles(page=page)
    return render_template('index.html', 
                         entries=articles,
                         page=page,
                         total_pages=(total_count + 29) // 30,
                         total_count=total_count)

@app.route('/source/<source>')
def source_page(source):
    page = request.args.get('page', 1, type=int)
    articles, total_count = get_articles(source=source, page=page)
    return render_template('index.html', 
                         entries=articles,
                         source=source,
                         page=page,
                         total_pages=(total_count + 29) // 30,
                         total_count=total_count)

@app.route('/tag/<tag>')
def tag_page(tag):
    page = request.args.get('page', 1, type=int)
    articles, total_count = get_articles(tag=tag, page=page)
    return render_template('index.html', 
                         entries=articles,
                         tag=tag,
                         page=page,
                         total_pages=(total_count + 29) // 30,
                         total_count=total_count)

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect('/')
        
    page = request.args.get('page', 1, type=int)
    articles, total_count = get_articles(search=query, page=page)
    return render_template('index.html', 
                         entries=articles,
                         search=query,
                         page=page,
                         total_pages=(total_count + 29) // 30,
                         total_count=total_count)

@app.route('/api/articles')
def api_articles():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    search = request.args.get('search', '')
    source = request.args.get('source', '')
    tag = request.args.get('tag', '')
    
    articles, total_count = get_articles(page=page, per_page=per_page, search=search, source=source, tag=tag)
    
    return jsonify({
        'articles': articles,
        'page': page,
        'has_more': len(articles) == per_page and (page * per_page) < total_count
    })

@app.route('/proxy/image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        logger.error("[PROXY] No URL provided to proxy")
        return 'No URL provided', 400
    
    try:
        # Common headers for all requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        # Parse the URL to get the domain
        parsed_url = urllib.parse.urlparse(url)
        domain = parsed_url.netloc
        
        # Add domain-specific headers
        if 'cdn.shopify.com' in domain:
            # For Shopify CDN, use Windup Watch Shop as referer
            headers['Referer'] = 'https://windupwatchshop.com/'
            headers['Origin'] = 'https://windupwatchshop.com'
        else:
            headers['Referer'] = f'https://{domain}/'
            headers['Origin'] = f'https://{domain}'
        
        # Special handling for Fratello images
        if 'fratellowatches.com' in domain:
            url = encode_fratello_url(url)
        
        # Make the request
        response = requests.get(url, headers=headers, timeout=10, stream=True, allow_redirects=True)
        response.raise_for_status()
        
        # Verify content type is an image
        content_type = response.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            logger.error(f"[PROXY] Invalid content type: {content_type}")
            return f'Invalid content type: {content_type}', 400
        
        # Create file-like object of the image
        image_io = BytesIO(response.content)
        
        # Send the image with proper headers
        response = send_file(
            image_io,
            mimetype=content_type,
            as_attachment=False,
            download_name=None,
            max_age=3600  # Cache for 1 hour
        )
        
        # Add CORS headers
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        
        return response
        
    except requests.exceptions.RequestException as e:
        logger.error(f"[PROXY] Request error for {url}: {str(e)}")
        return f'Error fetching image: {str(e)}', 502
    except Exception as e:
        logger.error(f"[PROXY] Error proxying image {url}: {str(e)}")
        return f'Error proxying image: {str(e)}', 500

def encode_fratello_url(url):
    """Special URL encoding for Fratello images."""
    parsed = urllib.parse.urlparse(url)
    path_segments = parsed.path.split('/')
    encoded_segments = []
    for segment in path_segments:
        if segment:
            if segment == path_segments[-1]:
                # Preserve special characters in filename
                encoded_segments.append(segment)
            else:
                encoded_segments.append(urllib.parse.quote(segment))
    
    encoded_path = '/'.join(s for s in encoded_segments if s)
    if not encoded_path.startswith('/'):
        encoded_path = '/' + encoded_path
    
    return urllib.parse.urlunparse((
        parsed.scheme or 'https',
        parsed.netloc,
        encoded_path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))

def init_db():
    conn = sqlite3.connect('articles.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            link TEXT UNIQUE NOT NULL,
            summary TEXT,
            published TIMESTAMP NOT NULL,
            source TEXT NOT NULL,
            image_url TEXT,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create indexes for faster querying
    c.execute('CREATE INDEX IF NOT EXISTS idx_published ON articles(published DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_source ON articles(source)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tags ON articles(tags)')
    
    # Update any existing entries with old source name
    c.execute('''
        UPDATE articles 
        SET source = 'Fratello' 
        WHERE source LIKE '%Fratello%' AND source != 'Fratello'
    ''')
    
    conn.commit()
    conn.close()

def store_article(entry, source):
    """Store an article in the database."""
    conn = sqlite3.connect('articles.db')
    c = conn.cursor()
    try:
        # Extract and clean tags
        tags = []
        entry_tags = entry.get('tags', [])
        
        # Handle both list and string inputs
        if isinstance(entry_tags, str):
            tags = [tag.strip() for tag in entry_tags.split(',') if tag.strip()]
        elif isinstance(entry_tags, list):
            for tag in entry_tags:
                if isinstance(tag, str) and tag.strip():
                    tags.append(tag.strip())
                elif isinstance(tag, dict):
                    if tag.get('term'):
                        tags.append(str(tag['term']).strip())
                    if tag.get('label'):
                        tags.append(str(tag['label']).strip())
        
        # Clean up tags and remove duplicates
        tags = list(set(tag for tag in tags if tag and len(tag) < 50))
        tags_str = ','.join(tags) if tags else None
        
        # Extract and process the image URL
        image_url = extract_image_from_entry(entry)
        if image_url:
            image_url = process_image_url(image_url, source)
        
        # Convert entry from feed format to database format
        article_data = {
            'title': entry.get('title', ''),
            'link': entry.get('link', ''),
            'summary': entry.get('summary', ''),
            'published': entry.get('published', datetime.now()),
            'source': entry.get('source', source),
            'image_url': image_url,
            'tags': tags_str
        }
        
        # Only store if we have required fields
        if article_data['title'] and article_data['link']:
            c.execute('''
                INSERT OR REPLACE INTO articles 
                (title, link, summary, published, source, image_url, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                article_data['title'],
                article_data['link'],
                article_data['summary'],
                article_data['published'],
                article_data['source'],
                article_data['image_url'],
                article_data['tags']
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"[STORE] Error storing article {entry.get('title', 'Unknown')}: {str(e)}")
    finally:
        conn.close()

def fetch_all_feeds():
    """Fetch all RSS feeds and return a dictionary of source to entries."""
    feeds = {
        'ABTW': 'https://www.ablogtowatch.com/feed/',
        'Fratello': 'https://www.fratellowatches.com/feed/',
        'Worn & Wound': 'https://wornandwound.com/feed/',
        'Monochrome': 'https://monochrome-watches.com/feed/',
        'Hodinkee': 'https://www.hodinkee.com/feed'
    }
    
    results = {}
    for source, url in feeds.items():
        try:
            logger.info(f"Fetching feed for {source} from {url}")
            feed = feedparser.parse(url)
            if feed.get('status') == 200 and feed.entries:
                results[source] = feed.entries
                logger.info(f"Successfully fetched {len(feed.entries)} entries from {source}")
            else:
                logger.warning(f"Failed to fetch feed for {source}: Status {feed.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"Error fetching feed for {source}: {str(e)}")
            results[source] = []
    
    return results

def background_feed_update():
    """Background thread function to periodically update feeds."""
    while True:
        try:
            feeds = fetch_all_feeds()
            for source, entries in feeds.items():
                for entry in entries:
                    try:
                        store_article(entry, source)
                    except Exception as e:
                        logger.error(f"Error storing article from {source}: {str(e)}")
        except Exception as e:
            logger.error(f"Error in background feed update: {str(e)}")
        
        time.sleep(3600)  # Sleep for 1 hour

def init_app():
    """Initialize the application."""
    try:
        init_db()
        
        # Fetch initial articles in parallel
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for source, config in FEEDS.items():
                def fetch_source_articles(src, cfg):
                    entries = fetch_feed(src, cfg)
                    for entry in entries:
                        store_article(entry, src)
                    return len(entries)
                
                futures.append(executor.submit(fetch_source_articles, source, config))
            
            # Wait for all fetches to complete
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Failed to fetch feed: {str(e)}")
        
        # Start background feed update thread with a delay
        def delayed_start():
            time.sleep(3600)  # Wait 1 hour before starting background updates
            background_feed_update()
        
        update_thread = threading.Thread(target=delayed_start, daemon=True)
        update_thread.start()
        
    except Exception as e:
        logger.error(f"Error initializing app: {str(e)}")
        raise

# Call init_app when the application starts
with app.app_context():
    init_app()

@app.route('/api/sources')
def api_sources():
    conn = sqlite3.connect('articles.db')
    c = conn.cursor()
    try:
        c.execute('SELECT DISTINCT source FROM articles ORDER BY source')
        sources = [row[0] for row in c.fetchall()]
        return jsonify({'sources': sources})
    finally:
        conn.close()

@app.route('/api/tags')
def api_tags():
    conn = sqlite3.connect('articles.db')
    c = conn.cursor()
    try:
        c.execute('SELECT DISTINCT tags FROM articles WHERE tags IS NOT NULL')
        tag_rows = c.fetchall()
        # Split comma-separated tags and create a unique set
        all_tags = set()
        for row in tag_rows:
            if row[0]:
                all_tags.update(tag.strip() for tag in row[0].split(','))
        return jsonify({'tags': sorted(list(all_tags))})
    finally:
        conn.close()

@app.route('/shop')
def shop():
    return render_template('shop.html')

@app.route('/archive')
def archive():
    return render_template('archive.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 