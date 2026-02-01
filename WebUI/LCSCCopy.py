import requests
import json
import re
from bs4 import BeautifulSoup

def get_lcsc_product_data(keyword):
    """
    根据关键字从立创商城搜索并爬取首个产品的详情
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    # 1. 搜索产品
    search_url = f"https://so.szlcsc.com/global.html?k={keyword}"
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"搜索请求失败: {str(e)}"}

    # 提取详情页URL
    # 尝试从 ld+json 提取
    soup = BeautifulSoup(response.text, 'html.parser')
    ld_json_tag = soup.find('script', type='application/ld+json')
    product_url = None
    
    if ld_json_tag:
        try:
            ld_data = json.loads(ld_json_tag.string)
            if 'itemListElement' in ld_data and len(ld_data['itemListElement']) > 0:
                product_url = ld_data['itemListElement'][0]['item'].get('offers', {}).get('url')
        except:
            pass
            
    if not product_url:
        # 尝试从 a 标签提取 (如果 ld+json 失败)
        a_tags = soup.find_all('a', href=re.compile(r'https://item.szlcsc.com/\d+\.html'))
        if a_tags:
            product_url = a_tags[0]['href']

    if not product_url:
        return {"success": False, "error": "未找到相关产品详情页"}

    # 2. 获取详情页数据
    try:
        detail_response = requests.get(product_url, headers=headers, timeout=10)
        detail_response.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"获取详情页失败: {str(e)}"}

    # 3. 解析 __NEXT_DATA__
    detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
    next_data_tag = detail_soup.find('script', id='__NEXT_DATA__')
    
    if not next_data_tag:
        return {"success": False, "error": "无法解析详情页数据结构 (__NEXT_DATA__ not found)"}

    try:
        data = json.loads(next_data_tag.string)
        web_data = data.get('props', {}).get('pageProps', {}).get('webData', {})
        product_record = web_data.get('productRecord', {})
        param_list = web_data.get('paramList', [])
        current_catalog = web_data.get('currentCatalog', {})
        
        # 提取参数
        params = {p['parameterName']: p['parameterValue'] for p in param_list}
        
        result = {
            "success": True,
            "product_name": product_record.get('productModel'),
            "功能类型": params.get('类型') or params.get('功能类型') or "N/A",
            "工作电压": params.get('工作电压') or "N/A",
            "输出电压": params.get('输出电压') or "N/A",
            "输出电流": params.get('输出电流') or "N/A",
            "描述": product_record.get('remark') or "N/A",
            "类目": current_catalog.get('catalogName') or product_record.get('productType') or "N/A"
        }
        return result
    except Exception as e:
        return {"success": False, "error": f"解析数据失败: {str(e)}"}

if __name__ == "__main__":
    import sys
    search_keyword = sys.argv[1] if len(sys.argv) > 1 else "ch224k"
    res = get_lcsc_product_data(search_keyword)
    print(json.dumps(res, indent=4, ensure_ascii=False))
