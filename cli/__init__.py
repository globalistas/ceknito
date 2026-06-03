from .recount import recount
from .admin import admin
from .default import default
from .migration import migration
from .route import route
from .translations import translations
from .user import user
from .export import export

commands = [migration, recount, route, admin, default, translations, user, export]
