# 导入Pillow库的Image模块（处理图片的核心模块）
from PIL import Image

# 定义拼接函数（横向拼接：左右拼）
def merge_two_images(img1_path, img2_path, output_path):
    # 1. 打开两张待拼接的图片
    img1 = Image.open(img1_path)
    img2 = Image.open(img2_path)
    
    # 2. 获取两张图片的宽、高（size返回元组：(宽度, 高度)）
    w1, h1 = img1.size
    w2, h2 = img2.size
    
    # 3. 计算拼接后新图片的尺寸：宽度=两张宽度之和，高度=取两张中较大值
    new_width = w1 + w2
    new_height = max(h1, h2)
    
    # 4. 创建空白画布（RGB模式，白色背景填充）
    new_img = Image.new('RGB', (new_width, new_height), color='white')
    
    # 5. 粘贴图片：第一张贴左侧(0,0)，第二张贴右侧(w1,0)
    new_img.paste(img1, (0, 0))
    new_img.paste(img2, (w1, 0))
    
    # 6. 保存拼接后的图片
    new_img.save(output_path)
    print(f"拼接完成！新图片已保存至：{output_path}")

# 主程序调用（替换为你自己的图片路径）
if __name__ == "__main__":
    # 示例：拼接img1.jpg和img2.jpg，保存为merged_image.jpg
    merge_two_images("dataset/1.jpeg", "dataset/2.jpeg", "output/merged_image.jpg")