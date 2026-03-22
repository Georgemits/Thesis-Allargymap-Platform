from flask import Flask
from flask_cors import CORS
from .config import config_by_name
from .extensions import mongo
import os


def create_app(config_name: str = None) -> Flask:
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    CORS(app)
    mongo.init_app(app)

    from .routes.health import bp as health_bp
    from .routes.reports import bp as reports_bp
    from .routes.env_data import bp as env_bp
    from .routes.predictions import bp as predictions_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(reports_bp, url_prefix="/api/reports")
    app.register_blueprint(env_bp, url_prefix="/api/env")
    app.register_blueprint(predictions_bp, url_prefix="/api/predictions")

    return app
