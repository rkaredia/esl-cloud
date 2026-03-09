import os
import markdown2
import logging
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.conf import settings
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)

HELP_CONTENT_DIR = os.path.join(settings.BASE_DIR, 'help_module', 'content')

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
    try:
        if topic_slug not in TOPICS:
            raise Http404("Topic not found")

        topic = TOPICS[topic_slug]
        file_path = os.path.join(HELP_CONTENT_DIR, topic['file'])

        if not os.path.exists(file_path):
            logger.error(f"Help file not found: {file_path}")
            raise Http404("Content file not found")

        with open(file_path, 'r', encoding='utf-8') as f:
            content_markdown = f.read()

        content_html = markdown2.markdown(content_markdown)

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
