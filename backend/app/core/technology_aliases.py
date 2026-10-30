"""Conservative technology-name canonicalization shared by staffing facts."""
import re
import unicodedata


# Keep this table explicit: similar-looking technologies must not be merged by
# fuzzy matching. Add a spelling here only when it is a true synonym or a
# versioned form that is intentionally matched by the general skill name.
ALIASES = {
    'py': 'python', 'python3': 'python', 'python 3': 'python',
    'js': 'javascript', 'java script': 'javascript',
    'ts': 'typescript', 'type script': 'typescript',
    'postgres': 'postgresql', 'postgre sql': 'postgresql',
    'springboot': 'spring boot', 'spring cloud': 'spring cloud',
    'springcloud': 'spring cloud', 'springsecurity': 'spring security',
    'vuejs': 'vue.js', 'vue js': 'vue.js', 'vue2': 'vue', 'vue 2': 'vue',
    'vue3': 'vue', 'vue 3': 'vue', 'reactjs': 'react', 'react js': 'react',
    'nodejs': 'node.js', 'node js': 'node.js',
    'fast api': 'fastapi', 'lang graph': 'langgraph', 'lang chain': 'langchain',
    'k8s': 'kubernetes',
}


def basic_form(value):
    value = unicodedata.normalize('NFKC', str(value)).strip().casefold()
    value = re.sub(r'[\u2010-\u2015\u2212]', '-', value)
    value = re.sub(r'[_-]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def normalize(value):
    basic = basic_form(value)
    compact = basic.replace(' ', '')
    return ALIASES.get(basic) or ALIASES.get(compact) or basic
