"""Pre-approved hosts for web_fetch / web_fetch_to_attachment.

Mirror of ``claude-code-ref/packages/builtin-tools/src/tools/WebFetchTool/
preapproved.ts``. These domains are considered safe-by-default for
LLM-driven content retrieval — no plan_gate friction needed when the user
asks the agent to read or archive content from them.

**Security boundary**: this list is for **GET-only fetches**. It does NOT
imply general network access. Tools that POST / upload (e.g. doc_upload_*)
should NOT inherit this list — some of these domains (huggingface.co,
kaggle.com, nuget.org) accept file uploads and would be exfil vectors if
the gate were lifted unconditionally.

Plan_gate v2 will use ``is_preapproved`` to skip the explicit-approval step
for ``web_fetch_to_attachment`` when the source URL falls in this set.
For now (Phase 2.7 v0.15), we expose the constant so callers can build a
risk-classifier without coupling on plan_gate internals.
"""

from __future__ import annotations

from urllib.parse import urlparse


# Tier 1: Anthropic ecosystem
_ANTHROPIC = (
    "platform.claude.com",
    "code.claude.com",
    "modelcontextprotocol.io",
    "agentskills.io",
)

# Tier 2: programming language official docs
_LANGUAGES = (
    "docs.python.org",
    "en.cppreference.com",
    "docs.oracle.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "go.dev",
    "pkg.go.dev",
    "www.php.net",
    "docs.swift.org",
    "kotlinlang.org",
    "ruby-doc.org",
    "doc.rust-lang.org",
    "www.typescriptlang.org",
)

# Tier 3: web / JS frameworks
_WEB_FRAMEWORKS = (
    "react.dev",
    "angular.io",
    "vuejs.org",
    "nextjs.org",
    "expressjs.com",
    "nodejs.org",
    "bun.sh",
    "jquery.com",
    "getbootstrap.com",
    "tailwindcss.com",
    "d3js.org",
    "threejs.org",
    "redux.js.org",
    "webpack.js.org",
    "jestjs.io",
    "reactrouter.com",
)

# Tier 4: Python ecosystem
_PYTHON_ECOSYSTEM = (
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "pandas.pydata.org",
    "numpy.org",
    "www.tensorflow.org",
    "pytorch.org",
    "scikit-learn.org",
    "matplotlib.org",
    "requests.readthedocs.io",
    "jupyter.org",
    "keras.io",
    "spark.apache.org",
    "huggingface.co",
    "www.kaggle.com",
)

# Tier 5: backend frameworks / build / mobile / databases / cloud / DevOps
_BACKEND = (
    "laravel.com",
    "symfony.com",
    "wordpress.org",
    "docs.spring.io",
    "hibernate.org",
    "tomcat.apache.org",
    "gradle.org",
    "maven.apache.org",
    "asp.net",
    "dotnet.microsoft.com",
    "nuget.org",
    "blazor.net",
    "reactnative.dev",
    "docs.flutter.dev",
    "developer.apple.com",
    "developer.android.com",
    "www.mongodb.com",
    "redis.io",
    "www.postgresql.org",
    "dev.mysql.com",
    "www.sqlite.org",
    "graphql.org",
    "prisma.io",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "kubernetes.io",
    "www.docker.com",
    "www.terraform.io",
    "www.ansible.com",
    "vercel.com",
    "docs.netlify.com",
    "devcenter.heroku.com",
    "cypress.io",
    "selenium.dev",
    "docs.unity.com",
    "docs.unrealengine.com",
    "git-scm.com",
    "nginx.org",
    "httpd.apache.org",
)

# Tier 6: Chinese policy / .gov.cn flavors that this RAG fork frequently
# operates on. Suffix matching kicks in via ``is_preapproved`` so e.g.
# ``szjs.sz.gov.cn`` is covered by the bare ``gov.cn`` entry.
_CN_GOV = (
    "gov.cn",
    "www.gov.cn",
    "sz.gov.cn",
    "people.com.cn",
    "xinhuanet.com",
    "chinanews.com.cn",
)

PREAPPROVED_HOSTS: frozenset[str] = frozenset(
    _ANTHROPIC
    + _LANGUAGES
    + _WEB_FRAMEWORKS
    + _PYTHON_ECOSYSTEM
    + _BACKEND
    + _CN_GOV
)


def is_preapproved(url: str) -> bool:
    """Returns True when the URL's host is on the preapproved list, with
    suffix matching: ``gov.cn`` covers both ``www.gov.cn`` and
    ``szjs.sz.gov.cn``."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in PREAPPROVED_HOSTS:
        return True
    # Suffix match: ``szjs.sz.gov.cn`` ⊃ ``gov.cn``.
    for entry in PREAPPROVED_HOSTS:
        if host.endswith("." + entry):
            return True
    return False
