"""
站点配置API
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, List, Optional

from app.core.site_config import get_site_config, get_all_site_configs, get_site_categories
from app.schemas.models import SiteConfigModel, SiteCategoryModel, ResponseModel

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("/", response_model=ResponseModel)
async def get_all_sites():
    """获取所有站点配置"""
    try:
        configs = get_all_site_configs()
        return ResponseModel(
            code=200,
            message="获取站点配置成功",
            data={"sites": configs}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取站点配置失败: {str(e)}")


@router.get("/{site_name}", response_model=ResponseModel)
async def get_site(site_name: str):
    """获取指定站点配置"""
    try:
        config = get_site_config(site_name)
        if not config:
            raise HTTPException(status_code=404, detail=f"站点 {site_name} 不存在")
        
        return ResponseModel(
            code=200,
            message="获取站点配置成功",
            data={"site": config}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取站点配置失败: {str(e)}")


@router.get("/{site_name}/categories", response_model=ResponseModel)
async def get_site_categories_endpoint(site_name: str):
    """获取站点分类"""
    try:
        categories = get_site_categories(site_name)
        if not categories:
            raise HTTPException(status_code=404, detail=f"站点 {site_name} 不存在或无分类")
        
        return ResponseModel(
            code=200,
            message="获取站点分类成功",
            data={"categories": categories}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取站点分类失败: {str(e)}")