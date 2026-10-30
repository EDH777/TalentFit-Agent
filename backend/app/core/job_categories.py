"""Primary resume categories; unknown is stored as NULL, never guessed."""
from typing import Literal, get_args

JobCategory = Literal['产品经理', '前端开发工程师', '后端开发工程师', '测试工程师',
                      'AI应用开发工程师', '现场实施工程师', '硬件工程师', '采购经理']
JOB_CATEGORIES = get_args(JobCategory)
JOB_CATEGORY_CHECK = 'job_category IS NULL OR job_category IN (' + ','.join(f"'{value}'" for value in JOB_CATEGORIES) + ')'
