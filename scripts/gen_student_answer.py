"""生成「学生作答」Word 文件，供试卷批改 Agent 测试 / 第6章 6.3 解析演示。

要点：
1) 学员答卷 = 「题头 + 答案」，不要把题目原文/选项再抄进去——否则会被解析器当成答案。
2) 代码题用 ``` 围栏包裹，解析器才能保留缩进和空行。
3) 这份答卷故意做了「有对有错」，且覆盖解析器的全部分支（见说明文档）。
用法：python gen_student_answer.py
"""
from docx import Document

doc = Document()
def P(t=""): doc.add_paragraph(t)

# 题1 单选｜题头「第X题」+ 行内「答：A」（对，标准答案 A）
P("第1题 final 关键字（单选）"); P("答：A"); P("")
# 题2 多选｜题头「题目X」格式 + 行内「答：AC」（错，漏选 D；标准答案 ACD）
P("题目2 Java 集合（多选）"); P("答：AC"); P("")
# 题3 判断｜中文题号「第三题」+「作答区」模板行（被跳过）+「答：正确」（对）
P("第三题 int 默认值（判断）"); P("作答区："); P("答：正确"); P("")
# 题4 简答｜题头「第X题」+「答：」空冒号 + 多行答案（部分对，漏掉"解耦"得分点）
P("第4题 解释 Spring IOC（简答）"); P("答：")
P("Spring IOC（控制反转）是一种设计思想，对象的创建和依赖关系不再由程序自己 new，")
P("而是交给 Spring 容器统一创建并注入。开发时只需声明依赖，由容器在运行时装配。"); P("")
# 题5 代码｜题头「Q.X」格式 + ``` 代码块（含缩进+空行，对）
P("Q.5 斐波那契（代码）"); P("```")
for line in [
    "public class Solution {",
    "    public int fib(int n) {",
    "        if (n <= 1) return n;",
    "",
    "        return fib(n - 1) + fib(n - 2);",
    "    }",
    "}",
]:
    P(line)
P("```")

doc.save("student_answer.docx")
print("已生成 student_answer.docx（全分支 + 有对有错）")
