"""
应用配置管理
"""
from pydantic import BaseSettings


class Settings(BaseSettings):
    """应用配置类"""
    
    # 数据库配置
    database_type: str = "sqlite"
    config_dir: str = "."
    
    # PostgreSQL配置
    db_host: str = "localhost"
    db_port: str = "5432"
    db_name: str = "moviepilot"
    db_user: str = "postgres"
    db_password: str = "postgres"
    
    # 应用配置
    app_name: str = "MoviePilot Server"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # 服务器配置
    host: str = "::"
    port: int = 3001
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    @property
    def database_url(self) -> str:
        """获取数据库连接URL"""
        if self.database_type.lower() == 'postgresql':
            return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
        else:
            return f"sqlite+aiosqlite:///{self.config_dir}/server.db"
    
    @property
    def is_postgresql(self) -> bool:
        """判断是否使用PostgreSQL"""
        return self.database_type.lower() == 'postgresql'


# 全局配置实例
settings = Settings()