import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY          = os.environ.get('SECRET_KEY', 'change-me-in-production-use-random-32-chars')
    DATABASE            = os.environ.get('DATABASE', os.path.join(BASE_DIR, 'instance', 'splitwise.db'))
    UPLOAD_FOLDER       = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploads'))
    MAX_CONTENT_LENGTH  = 10 * 1024 * 1024   # 10 MB
    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SAMESITE  = 'Lax'
    # Set to True behind HTTPS in production
    SESSION_COOKIE_SECURE    = os.environ.get('HTTPS', 'false').lower() == 'true'

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}
