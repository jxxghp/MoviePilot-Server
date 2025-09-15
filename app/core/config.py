"""
应用配置管理
"""
import os
from typing import Optional


class Settings:
    """应用配置类"""
    
    # 数据库配置
    DATABASE_TYPE: str = os.getenv('DATABASE_TYPE', 'sqlite').lower()
    CONFIG_DIR: str = os.getenv('CONFIG_DIR', '.')
    
    # PostgreSQL配置
    DB_HOST: str = os.getenv('DB_HOST', 'localhost')
    DB_PORT: str = os.getenv('DB_PORT', '5432')
    DB_NAME: str = os.getenv('DB_NAME', 'moviepilot')
    DB_USER: str = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD: str = os.getenv('DB_PASSWORD', 'postgres')
    
    # 应用配置
    APP_NAME: str = "MoviePilot Server"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv('DEBUG', 'false').lower() == 'true'
    
    # 服务器配置
    HOST: str = os.getenv('HOST', '::')
    PORT: int = int(os.getenv('PORT', '3001'))
    
    @property
    def database_url(self) -> str:
        """获取数据库连接URL"""
        if self.DATABASE_TYPE == 'postgresql':
            return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        else:
            return f"sqlite+aiosqlite:///{self.CONFIG_DIR}/server.db"
    
    @property
    def is_postgresql(self) -> bool:
        """判断是否使用PostgreSQL"""
        return self.DATABASE_TYPE == 'postgresql'


# 全局配置实例
settings = Settings()