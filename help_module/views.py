import os
import markdown2
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.conf import settings
from django.utils.safestring import mark_safe

HELP_CONTENT_DIR = os.path.join(settings.BASE_DIR, 'help_module', 'content')

TOPICS = {
    'product-management': {'title': 'Product Management', 'file': 'product_management.md'},
    'tag-manager': {'title': 'Tag Manager', 'file': 'tag_manager.md'},
    'linkage': {'title': 'Product to Tag Linkage', 'file': 'linkage.md'},
    'access-management': {'title': 'Access Management', 'file': 'access_management.md'},
    'troubleshooting': {'title': 'Troubleshooting', 'file': 'troubleshooting.md'},
    'standard-errors': {'title': 'Standard Errors', 'file': 'standard_errors.md'},
    'dashboard': {'title': 'Analytics Dashboard', 'file': 'dashboard.md'},
    'store-context': {'title': 'Store Context & Isolation', 'file': 'store_context.md'},
}

@login_required
def help_index(request):
    return render(request, 'help_module/index.html', {
        'topics': TOPICS,
        'title': 'SAIS Help Center'
    })

@login_required
def help_detail(request, topic_slug):
    if topic_slug not in TOPICS:
        raise Http404("Topic not found")

    topic = TOPICS[topic_slug]
    file_path = os.path.join(HELP_CONTENT_DIR, topic['file'])

    if not os.path.exists(file_path):
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
