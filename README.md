# Watch News Aggregator

A modern web application that aggregates news from popular watch blogs and websites including Hodinkee, Worn & Wound, A Blog To Watch, and Fratello.

## Features

- Real-time RSS feed aggregation
- Clean, responsive UI using Tailwind CSS
- Automatic feed refresh
- Article sorting by publication date
- Caching system for improved performance

## Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd <repository-name>
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

1. Start the Flask development server:
```bash
python app.py
```

2. Open your browser and navigate to:
```
http://localhost:5000
```

## Technical Details

- Built with Flask
- Uses feedparser for RSS feed parsing
- Implements Flask-Caching for performance optimization
- Responsive design with Tailwind CSS

## Contributing

Feel free to submit issues and enhancement requests! 