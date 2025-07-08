#!/usr/bin/env python3
"""
工作流分享功能测试脚本
"""
import json
import requests
from datetime import datetime

# 服务器地址
BASE_URL = "http://localhost:3001"

def test_workflow_share():
    """测试工作流分享功能"""
    
    # 测试数据
    workflow_data = {
        "share_title": "自动化下载工作流",
        "share_comment": "这是一个用于自动下载电影和电视剧的工作流",
        "share_user": "测试用户",
        "share_uid": "test_user_001",
        "name": "自动化下载工作流",
        "description": "自动监控并下载新发布的电影和电视剧",
        "timer": "0 */6 * * *",  # 每6小时执行一次
        "actions": json.dumps([
            {"name": "检查新发布", "type": "check_new"},
            {"name": "下载文件", "type": "download"},
            {"name": "通知用户", "type": "notify"}
        ]),
        "flows": json.dumps([
            {"from": "检查新发布", "to": "下载文件", "condition": "has_new"},
            {"from": "下载文件", "to": "通知用户", "condition": "download_success"}
        ]),
        "context": json.dumps({
            "download_path": "/downloads",
            "notification_method": "email"
        })
    }
    
    print("=== 工作流分享功能测试 ===")
    
    # 1. 测试新增工作流分享
    print("\n1. 测试新增工作流分享...")
    response = requests.post(f"{BASE_URL}/workflow/share", json=workflow_data)
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")
    
    # 2. 测试查询工作流分享列表
    print("\n2. 测试查询工作流分享列表...")
    response = requests.get(f"{BASE_URL}/workflow/shares")
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")
    
    # 3. 测试复用工作流
    print("\n3. 测试复用工作流...")
    if response.status_code == 200 and response.json():
        share_id = response.json()[0].get('id')
        if share_id:
            response = requests.get(f"{BASE_URL}/workflow/fork/{share_id}")
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.json()}")
    
    # 4. 测试删除工作流分享
    print("\n4. 测试删除工作流分享...")
    if response.status_code == 200 and response.json():
        share_id = response.json()[0].get('id')
        if share_id:
            response = requests.delete(f"{BASE_URL}/workflow/share/{share_id}?share_uid=test_user_001")
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.json()}")
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    test_workflow_share()