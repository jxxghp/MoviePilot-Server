"""
应用配置管理
"""
from pydantic import BaseSettings


class Settings(BaseSettings):
    """应用配置类"""
    
    # 数据库配置
    DATABASE_TYPE: str = "sqlite"
    CONFIG_DIR: str = "."
    
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
    HOST: str = "::"
    PORT: int = 3001
    
    # TheMovieDB API配置
    TMDB_API_KEY: str = ""
    TMDB_API_URL: str = "https://api.themoviedb.org/3"
    TMDB_TIMEOUT: int = 10
    
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


# 全局配置实例
settings = Settings()