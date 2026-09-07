from flask import Flask
from flask_cors import CORS
from .config import config_by_name
from .extensions import mongo
from .utils.identity import DEVICE_ID_HEADER
import os


def create_app(config_name: str = None) -> Flask:
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # The anonymous device identity travels in a custom request header, so
    # it has to be allowlisted explicitly: a browser will not send a custom
    # header cross-origin unless the CORS preflight response names it.
    CORS(app, allow_headers=["Content-Type", DEVICE_ID_HEADER])
    mongo.init_app(app)

    from .routes.health import bp as health_bp
    from .routes.reports import bp as reports_bp
    from .routes.env_data import bp as env_bp
    from .routes.predictions import bp as predictions_bp
    from .routes.users import bp as users_bp
    from .routes.profiles import bp as profiles_bp
    from .routes.auth import bp as auth_bp
    from .routes.correlations import bp as correlations_bp
    from .routes.site import bp as site_bp, frontend_dir

    app.register_blueprint(health_bp)
    app.register_blueprint(reports_bp, url_prefix="/api/reports")
    app.register_blueprint(env_bp, url_prefix="/api/env")
    app.register_blueprint(predictions_bp, url_prefix="/api/predictions")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(profiles_bp, url_prefix="/api/profiles")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(correlations_bp, url_prefix="/api/correlations")

    # Single-service deployment: when the frontend ships alongside the API,
    # serve it from the same origin. Registered last so it can claim "/" and
    # the catch-all path without shadowing any /api route.
    directory = frontend_dir()
    if directory is not None:
        app.config["FRONTEND_DIR"] = str(directory)
        app.register_blueprint(site_bp)

    return app
