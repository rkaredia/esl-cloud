import os
import markdown2
import logging
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.conf import settings
from django.utils.safestring import mark_safe

"""
HELP MODULE VIEWS: DYNAMIC DOCUMENTATION ENGINE
-----------------------------------------------
This module provides a simple way to manage user documentation
without editing database tables.

HOW IT WORKS:
1. Markdown (.md) files are stored in the 'help_module/content/' folder.
2. When a user clicks a topic, this view reads the corresponding .md file.
3. It uses the 'markdown2' library to convert the text into HTML.
4. The HTML is then displayed inside the SAIS Admin theme.

This allows non-developers to update the help guides just by editing
simple text files.
"""

logger = logging.getLogger(__name__)

# Identify where the physical Markdown files live on the disk
HELP_CONTENT_DIR = os.path.join(settings.BASE_DIR, 'help_module', 'content')

# THE TOPIC REGISTRY: Maps URL slugs to human titles and file names.
TOPICS = {
    'getting-started': {'title': 'Getting Started', 'file': 'getting_started.md'},
    'product-management': {'title': 'Product Management', 'file': 'product_management.md'},
    'tag-manager': {'title': 'Tag Manager', 'file': 'tag_manager.md'},
    'linkage': {'title': 'Product to Tag Linkage', 'file': 'linkage.md'},
    'dashboard': {'title': 'Analytics Dashboard', 'file': 'dashboard.md'},
    'store-context': {'title': 'Store Context & Isolation', 'file': 'store_context.md'},
    'troubleshooting': {'title': 'Troubleshooting & Errors', 'file': 'troubleshooting.md'},
    'access-management': {'title': 'Access Management', 'file': 'access_management.md'},
}

@login_required
def help_index(request):
    """
    HELP CENTER HOME
    ----------------
    Displays the grid of all available help topics.
    """
    try:
        return render(request, 'help_module/index.html', {
            'topics': TOPICS,
            'title': 'SAIS Help Center'
        })
    except Exception:
        logger.exception("Error in help_index view")
        raise Http404("Error loading help center")

@login_required
def help_detail(request, topic_slug):
    """
    GUIDE DETAIL VIEW
    -----------------
    Reads a Markdown file from the disk, converts it to HTML,
    and renders it.
    """
    try:
        # 1. Validation: Does the slug exist in our TOPICS registry?
        if topic_slug not in TOPICS:
            raise Http404("Topic not found")

        topic = TOPICS[topic_slug]
        file_path = os.path.join(HELP_CONTENT_DIR, topic['file'])

        # 2. Disk Check: Ensure the file actually exists in the content folder
        if not os.path.exists(file_path):
            logger.error(f"Help file not found: {file_path}")
            raise Http404("Content file not found")

        # 3. Read the raw Markdown text
        with open(file_path, 'r', encoding='utf-8') as f:
            content_markdown = f.read()

        # 4. Conversion: Transform Markdown (e.g., # Title) into HTML (e.g., <h1>Title</h1>)
        content_html = markdown2.markdown(content_markdown)

        # 5. Render: Send the HTML to the template.
        # EDUCATIONAL: mark_safe() tells Django NOT to escape the HTML tags,
        # allowing the browser to render the markdown styling.
        return render(request, 'help_module/detail.html', {
            'title': topic['title'],
            'content': mark_safe(content_html),
            'topics': TOPICS,
            'active_topic': topic_slug
        })
    except Http404:
        raise
    except Exception:
        logger.exception(f"Error in help_detail for {topic_slug}")
        raise Http404("Error rendering help topic")
