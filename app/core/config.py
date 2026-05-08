"""
应用配置管理
"""
from urllib.parse import quote

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置类"""

    # 数据库配置
    DATABASE_TYPE: str = "sqlite"
    CONFIG_DIR: str = "."

    # Redis配置
    REDIS_URL: str = ""
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_USERNAME: str = ""
    REDIS_PASSWORD: str = ""
    REDIS_SSL: bool = False
    REDIS_MAX_CONNECTIONS: int = 50
    REDIS_CONNECT_TIMEOUT: int = 5
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_KEY_PREFIX: str = "moviepilot"

    # PostgreSQL配置
    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"
    DB_NAME: str = "moviepilot"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"

    # 应用配置
    APP_NAME: str = "MoviePilot Server"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 3001

    # TheMovieDB API配置
    TMDB_API_KEY: str = "db55323b8d3e4154498498a75642b381"
    TMDB_API_URL: str = "https://api.themoviedb.org/3"
    TMDB_TIMEOUT: int = 10

    # 115网盘 OAuth2配置
    U115_CLIENT_ID: str = ""
    U115_CLIENT_SECRET: str = ""
    U115_REDIRECT_URI: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def database_url(self) -> str:
        """获取数据库连接URL"""
        if self.DATABASE_TYPE.lower() == 'postgresql':
            return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        else:
            return f"sqlite+aiosqlite:///{self.CONFIG_DIR}/server.db"

    @property
    def is_postgresql(self) -> bool:
        """判断是否使用PostgreSQL"""
        return self.DATABASE_TYPE.lower() == 'postgresql'

    @property
    def redis_url(self) -> str:
        """获取Redis连接URL"""
        if self.REDIS_URL:
            return self.REDIS_URL

        protocol = "rediss" if self.REDIS_SSL else "redis"
        auth = ""
        if self.REDIS_USERNAME and self.REDIS_PASSWORD:
            auth = f"{quote(self.REDIS_USERNAME, safe='')}:{quote(self.REDIS_PASSWORD, safe='')}@"
        elif self.REDIS_PASSWORD:
            auth = f":{quote(self.REDIS_PASSWORD, safe='')}@"
        elif self.REDIS_USERNAME:
            auth = f"{quote(self.REDIS_USERNAME, safe='')}@"

        return f"{protocol}://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def media_recognize_share_redis_prefix(self) -> str:
        """共享识别缓存Redis前缀"""
        return f"{self.REDIS_KEY_PREFIX}:media_recognize_share"


# 全局配置实例
settings = Settings()
