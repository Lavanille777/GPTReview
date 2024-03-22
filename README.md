# 使用 AI Review 代码

## Simple But Useful

## 首次使用前需要安装依赖

在gptReview目录下

```
pip3 install -U -r requirements.txt
```

## Config

+ api_key : 你的api key
+ api_base : 你的api host 默认为openai官方地址，记得带上/v1
+ commit1、commit2: 如果你想审查两次提交之间的差异，同时填上这两个提交的HASH。如不填或漏填，将默认审查当前git diff的内容。
+ model: 你要使用的模型名称，常用的模型列表有gpt-3.5-turbo, gpt-3.5-turbo-instruct, gpt-3.5-turbo-16k, gpt-4-turbo-preview, claude-3-haiku-20240307, claude-3-sonnet-20240229, claude-3-opus-20240229 等等。
+ ex_prompt: 你可以额外添加一些提示词，也可以不写，例如"这是一份iOS代码，重点关注内存泄露的问题"
+ repo_path: 你的本地仓库目录，直接把文件夹拖进来就行，如果不填，默认仓库在上级目录。
+ export_type: 报告导出的格式，md, html, pdf，默认为pdf。
+ export_path: 报告导出的目录，默认为桌面。

## 开始

只需将gptReview拖到仓库目录下（但记得在gitignore中忽略掉这个文件夹），或是在config中指定仓库目录，然后运行：

```
python3 gitAnalasis.py
```

如果你的改动太长，文本将拆分成几段串行发送。

等待一段时间后报告将自动打开。

