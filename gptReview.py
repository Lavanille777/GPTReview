import subprocess
import os
import enum
import json
import inspect
import argparse
import datetime
import requests
import markdown
from weasyprint import HTML
from functools import partial
from argparse import Namespace
from typing import List

from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.table import Table

import cmd2
from cmd2 import argparse_custom, with_argparser, Settable
import openai

class ContextLevel(enum.Enum):
    NONE = 0
    REQUEST = 1
    FULL =  2

class Config:
    sep = Markdown("---")
    baseDir = os.path.dirname(os.path.realpath(__file__))
    default = os.path.join(baseDir, "config.json")
    mdSep = '\n\n' + '-' * 10 + '\n'
    encodings = ["utf8", "gbk"]

    def __init__(self, file=None) -> None:
        self.cfg = {}
        if file:
            self.load(file)

    def load(self, file):
        with open(file, "r") as f:
            self.cfg = json.load(f)
        c: dict = self.cfg
        self.api_key = c.get("api_key") or openai.api_key
        self.api_base = c.get("api_base") or openai.api_base
        self.api_type = c.get("api_type") or openai.api_type
        self.api_version = c.get("api_version") or openai.api_version
        self.api_organization = c.get("api_organization") or openai.organization
        self.model = c.get("model", "gpt-3.5-turbo")
        self.prompt = c.get("prompt", [])
        self.stream = c.get("stream", False)
        self.stream_render = c.get("stream_render", False)
        self.context = ContextLevel(c.get("context", 0))
        self.proxy = c.get("proxy", "")
        self.showtokens = c.get("showtokens", False)
        self.path = c.get("repo_path", "")
        self.export_type = c.get("export_type", "pdf")
        self.commit1 = c.get("commit1", "")
        self.commit2 = c.get("commit2", "")
        self.export_path = c.get("export_path", "")
        self.ex_prompt = c.get("ex_prompt", "")

    def get(self, key, default=None):
        return self.cfg.get(key, default)


class GptCli(cmd2.Cmd):
    prompt = "gptcli> "

    extraSys = ""

    def __init__(self, config):
        super().__init__(
            allow_cli_args=False,
            allow_redirection=False,
            shortcuts={},
            persistent_history_file=os.path.expanduser("~/.gptcli_history"),
        )
        self.aliases[".exit"] = ".quit"
        self.aliases[".config"] = ".set"
        self.doc_header = "gptcli commands (use '.help -v' for verbose/'.help <topic>' for details):"
        self.hidden_commands = [
            "._relative_run_script", ".run_script", ".run_pyscript",
            ".eof", ".history", ".macro", ".shell", ".shortcuts", ".alias"]
        for sk in ["allow_style", "always_show_hint", "echo", "feedback_to_output",
                  "max_completion_items", "quiet", "timing"]:
            self.remove_settable(sk)
        self.console = Console()
        self.session = []
        # Init config
        self.print("Loading config from:", config)
        self.config = Config(config)
        for opt in ["key", "base", "type", "version", "organization"]:
            opt = f"api_{opt}"
            val = getattr(self.config, opt)
            setattr(openai, opt, val)
            if opt == "api_key" and len(val) > 7:
                val = val[:7] + "*" * 5
            self.print(f"openai.{opt}={val}")
        if self.config.proxy:
            self.print("Proxy:", self.config.proxy)
            openai.proxy = self.config.proxy
        self.print("Context level:", self.config.context)
        self.print("Stream mode:", self.config.stream)
        # Init settable
        # NOTE: proxy is not settable in runtime since openai use pre-configured session
        self.add_settable(Settable("api_key", str, "OPENAI_API_KEY", self.config, onchange_cb=self.openai_set))
        self.add_settable(Settable("api_base", str, "OPENAI_API_BASE", self.config, onchange_cb=self.openai_set))
        self.add_settable(Settable("api_type", str, "OPENAI_API_TYPE", self.config, onchange_cb=self.openai_set,
                                   choices=("open_ai", "azure", "azure_ad", "azuread")))
        self.add_settable(Settable("api_version", str, "OPENAI_API_VERSION", self.config, onchange_cb=self.openai_set))
        self.add_settable(Settable("api_organization", str, "OPENAI_API_ORGANIZATION", self.config, onchange_cb=self.openai_set))
        self.add_settable(Settable("context", lambda v: ContextLevel(int(v)), "Session context mode",
                                   self.config, completer=partial(cmd2.Cmd.basic_complete, match_against="012")))
        self.add_settable(Settable("stream", bool, "Enable stream mode", self.config))
        self.add_settable(Settable("stream_render", bool, "Render live markdown in stream mode", self.config))
        self.add_settable(Settable("model", str, "OPENAI model", self.config))
        self.add_settable(Settable("showtokens", bool, "Show tokens used with the output", self.config))
        # MISC
        # with self.console.capture() as capture:
        #     self.print(f"[bold yellow]{self.prompt}[/]", end="")
        # self.prompt = capture.get()

        self.single_tokens_used = 0
        self.total_tokens_used  = 0

    def openai_set(self, param, old, new):
        # self.print(f"openai.{param} = {old} -> {new}")
        setattr(openai, param, new)

    def onecmd_plus_hooks(self, line: str, *args, **kwargs) -> bool:
        """
        Dirty hack to use Cmd2 as chat console, and avoid statement parsing
        for chat input which may result in `No closing quotation` error.
        """
        if line.startswith("."):
            return super().onecmd_plus_hooks(line, *args, **kwargs)
        self.handle_input(line)
        return False

    def default(self, statement: cmd2.Statement):
        """
        for user input that startswith "." and not a recognized command,
        treat it as chat instead of print error message.
        """
        self.handle_input(statement.raw)

    def cmd_func(self, command: str):
        """
        Another hack to make command startswith "." and keep completer
        """
        if command.startswith("."):
            command = command[1:]
            return super().cmd_func(command)
        if inspect.currentframe().f_back.f_code.co_name == "_register_subcommands":
            return super().cmd_func(command)
        return None

    def get_all_commands(self) -> List[str]:
        return list(map(lambda c: f".{c}", super().get_all_commands()))

    def print(self, *msg, **kwargs):
        self.console.print(*msg, **kwargs)

    def handle_input(self, content: str, slice: int) -> str:
        if not content:
            return "啊哦，你好像没有输入任何内容"
        self.extraSys = ""
        if slice > 0:
            self.extraSys = f"由于用户的改动比较多，因此分成了{slice}段，一段段发送给你，不要回复任何关于分段的描述。标题为'代码审查报告第n部分'，到最后一部分再写总结！！"
        self.session.append({"role": "user", "content": content})
        if self.config.stream:
            answer = self.query_openai_stream(self.messages)
        else:
            answer = self.query_openai(self.messages)
        if not answer:
            self.session.pop()
            return "啊哦，好像没有返回任何内容"
        else:
            self.session.append({"role": "assistant", "content": answer})
            return answer

        # if self.config.showtokens:
        #     self.console.log(f"Tokens used: {self.single_tokens_used}")

    @property
    def messages(self):
        msgs = []
        msgs.extend([{"role": "system", "content": "以下是一段git仓库提交前的git diff内容，帮助用户review这段代码，找出其中所有的问题，包括但不限于：内存泄露，拼写错误，命名偏差，逻辑冗余等等，并以 markdown 格式用中文生成你的review报告(不要在回复中注明 Markdown)。注意指明位置，指出行号和代码。"}])
        if self.config.ex_prompt != "":
            msgs.extend([{"role": "system", "content": self.config.ex_prompt}])
        if self.extraSys != "":
            msgs.extend([{"role": "system", "content": self.extraSys}])
        if self.config.context == ContextLevel.FULL:
            msgs.extend(self.session)
        elif self.config.context == ContextLevel.REQUEST:
            msgs.extend([s for s in self.session if s["role"] != "assistant"])
        else: # NO Context
            msgs.append(self.session[-1])
        return msgs

    def load_session(self, file, mode="md", encoding=None, append=False):
        if not append:
            self.session.clear()
        with open(file, "r", encoding=encoding) as f:
            data = f.read()
        if mode == "json":
            self.session.extend(json.loads(data))
        elif mode == "md":
            for chat in data.split(Config.mdSep):
                role, content = chat.split(": ", 1)
                self.session.append({"role": role, "content": content})
        self.print("Load {} records from {}".format(len(self.session), file))

    def save_session(self, file, mode="md", encoding=None):
        self.print("Save {} records to {}".format(len(self.session), file))
        if mode == "json":
            data = json.dumps(self.session, indent=2)
        elif mode == "md":
            chats = ["{}: {}".format(chat["role"], chat["content"])
                     for chat in self.session]
            data = Config.mdSep.join(chats)
        with open(file, "w", encoding=encoding) as f:
            f.write(data)
    
    # Reference:
    # https://platform.openai.com/docs/guides/chat/managing-tokens
    def num_tokens_from_messages(self, messages):
        """Returns the number of tokens used by a list of messages."""
        import tiktoken
        model = self.config.model
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        if model not in ["gpt-3.5-turbo", "gpt-4", "gpt-4-32k"]:  # note: future models may deviate from this
            self.print(f"""num_tokens_from_messages() is not presently implemented for model {model}.
        See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens.""")
        num_tokens = 0
        for message in messages:
            num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens

    def query_openai(self, messages) -> str:
        try:
            response = openai.ChatCompletion.create(
                model=self.config.model,
                messages=messages
            )
            content = response["choices"][0]["message"]["content"]
            self.print(Markdown(content), Config.sep)

            self.single_tokens_used = response["usage"]["total_tokens"]
            self.total_tokens_used += self.single_tokens_used
            return content
        except openai.error.OpenAIError as e:
            self.print("OpenAIError:", e)
        return ""

    def query_openai_stream(self, messages) -> str:
        answer = ""
        try:
            response = openai.ChatCompletion.create(
                model=self.config.model,
                messages=messages,
                stream=True)
            with Live(auto_refresh=False, vertical_overflow="visible") as lv:
                for part in response:
                    finish_reason = part["choices"][0]["finish_reason"]
                    if "content" in part["choices"][0]["delta"]:
                        content = part["choices"][0]["delta"]["content"]
                        answer += content
                        if self.config.stream_render:
                            lv.update(Markdown(answer), refresh=True)
                        else:
                            lv.update(answer, refresh=True)
                    elif finish_reason:
                        if answer:
                            lv.update(Markdown(answer), refresh=True)

        except KeyboardInterrupt:
            self.print("Canceled")
        except openai.error.OpenAIError as e:
            self.print("OpenAIError:", e)
            answer = ""
        self.print(Config.sep)
        self.single_tokens_used = self.num_tokens_from_messages(messages + [{"role": "assistant", "content": answer}])
        self.total_tokens_used += self.single_tokens_used
        return answer

    parser_ml = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    @with_argparser(parser_ml)
    def do_multiline(self, args):
        "input multiple lines, end with ctrl-d(Linux/macOS) or ctrl-z(Windows). Cancel with ctrl-c"
        contents = []
        while True:
            try:
                line = input("> ")
            except EOFError:
                self.print("--- EOF ---")
                break
            except KeyboardInterrupt:
                self.print("^C")
                return
            contents.append(line)
        self.handle_input("\n".join(contents))

    parser_reset = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    @with_argparser(parser_reset)
    def do_reset(self, args):
        "Reset session, i.e. clear chat history"
        self.session.clear()
        self.print("session cleared.")

    parser_prompt = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    parser_prompt.add_argument("-c", dest="clear", action="store_true", help="remove current prompt")
    parser_prompt.add_argument("file", nargs="?", help="prompt file to load, can be plaintext or json format",
                               completer=cmd2.Cmd.path_complete)
    @with_argparser(parser_prompt)
    def do_prompt(self, args: Namespace):
        "Load different prompts"
        if args.clear:
            self.config.prompt.clear()
            self.print("Prompt cleared.")
        elif args.file:
            prompt = []
            if args.file.endswith(".json"):
                self.print("Load prompt from json")
                with open(args.file, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    prompt.extend(data)
                elif isinstance(data, dict):
                    prompt.append(data)
            else:
                self.print("Load prompt from text")
                with open(args.file, "r") as f:
                    data = f.read().rstrip()
                prompt.append(
                    { "role": "system", "content": data }
                )
            self.print("Prompt loaded:", json.dumps(prompt, indent=2, ensure_ascii=False))
            self.config.prompt = prompt
        else:
            self.print("Current prompt:", json.dumps(self.config.prompt, indent=2, ensure_ascii=False))

    parser_save = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    parser_save.add_argument("-m", dest="mode", choices=["json", "md"],
                             default="md", help="save as json or markdown (default: md)")
    parser_save.add_argument("-e", dest="encoding", choices=Config.encodings,
                             default=Config.encodings[0], help="file encoding")
    parser_save.add_argument("file", help="target file to save",
                            completer=cmd2.Cmd.path_complete)
    @with_argparser(parser_save)
    def do_save(self, args: Namespace):
        "Save current conversation to Markdown/JSON file"
        self.save_session(args.file, args.mode, args.encoding)

    parser_load = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    parser_load.add_argument("-a", dest="append", action="store_true",
                             help="append to current chat, by default current chat will be cleared")
    parser_load.add_argument("-m", dest="mode", choices=["json", "md"],
                             default="md", help="load as json or markdown (default: md)")
    parser_load.add_argument("-e", dest="encoding", choices=Config.encodings,
                             default=Config.encodings[0], help="file encoding")
    parser_load.add_argument("file", help="target file to load",
                            completer=cmd2.Cmd.path_complete)
    @with_argparser(parser_load)
    def do_load(self, args: Namespace):
        "Load conversation from Markdown/JSON file"
        self.load_session(args.file, args.mode, args.encoding, args.append)

    parser_usage = argparse_custom.DEFAULT_ARGUMENT_PARSER()
    parser_usage.add_argument("-d", dest="days", type=int,
                             help="print usage of last n days")
    parser_usage.add_argument("-b", dest="billing", action="store_true",
                             help="print detail of the billing subscription")
    @with_argparser(parser_usage)
    def do_usage(self, args: Namespace):
        "Tokens usage of current session / last N days, or print detail billing info"
        if args.days is None and not args.billing:
            self.print(f"Total tokens used this session: {self.total_tokens_used}")
            return
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        proxies = {}
        if self.config.proxy:
            proxies["http"] = self.config.proxy
            proxies["https"] = self.config.proxy
        if args.days:
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(args.days)
            url = f"{self.config.api_base}/dashboard/billing/usage"
            params = {
                "start_date": str(start_date.date()),
                "end_date": str(end_date.date()),
            }
            resp = requests.get(url, params=params, headers=headers, proxies=proxies)
            if resp.status_code != 200 or "json" not in resp.headers["content-type"]:
                self.print("Failed to get usage:", resp.status_code, resp.text)
                return
            js = resp.json()
            daily_costs = js.get("daily_costs")
            if not daily_costs:
                self.print("json error:", js)
                return
            table = Table()
            for i, cost in enumerate(daily_costs):
                line_items = cost.get("line_items", [])
                if i == 0:
                    table.add_column("time")
                    for item in line_items:
                        table.add_column(item["name"])
                row = [datetime.datetime.fromtimestamp(cost["timestamp"])] + [item["cost"] for item in line_items]
                table.add_row(*list(map(str, row)))
            self.print(table)
            self.print("total_usage", js.get("total_usage"))
        elif args.billing:
            url = f"{self.config.api_base}/dashboard/billing/subscription"
            resp = requests.get(url, headers=headers, proxies=proxies)
            self.console.print_json(resp.text)
            
def split_string_by_length(s, length):
    # 确保分片长度是正整数
    if not isinstance(length, int) or length <= 0:
        raise ValueError("Length must be a positive integer")

    # 使用列表推导式分片字符串
    return [s[i:i+length] for i in range(0, len(s), length)]

def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-c", dest="config", help="path to config.json", default=Config.default)
    args = parser.parse_args()
    config = Config(args.config)
    if config.path == "":
        os.chdir('..')
    else:
        os.chdir(config.path)
    # 尝试执行git diff命令
    try:
        gpt = GptCli(args.config)
        process = subprocess.Popen(['git', 'diff'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if config.commit1 != "" and config.commit2 != "":
            process = subprocess.Popen(['git', 'diff', config.commit1, config.commit2], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 获取命令输出和错误输出
        stdout, stderr = process.communicate()
        
        # 检查进程是否成功结束
        if process.returncode != 0:
            print(f"Error executing git diff: {stderr.decode()}")
        else:
        # 将命令输出转换为字符串
            diff_output = stdout.decode()
        # 输出结果（如果需要）
            print("正在阅读你的代码...")
        # 分片长度
            slice_length = 6000
            text = ''
            if len(diff_output) > slice_length:
                # 分片字符串
                slices = split_string_by_length(diff_output, slice_length)
                print(f"你的改动比较多，我将它分成了{len(slices)}段")
                # 打印结果
                for i, slice in enumerate(slices):
                    print(f"正在分析第{i+1}段")
                    input = f"以下是第{i+1}段: \n{slice}"
                    text += gpt.handle_input(input, len(slices))
            else:
                text = gpt.handle_input(diff_output, 0)
                
            export_path = os.path.join(os.path.expanduser('~'), 'Desktop')
            if config.export_path != "":
                export_path = config.export_path
            filename = "gpt_analasis"
            filename += f".{config.export_type}"
            file_path = os.path.join(export_path, filename)
            html = markdown.markdown(text, extensions=["tables", "fenced_code"])
            if config.export_type == "md":
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.write(text)
            elif config.export_type == "html":
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.write(html)
            else:
                HTML(string=html).write_pdf(file_path)
            
        # 使用Mac的open命令打开文件
            os.system(f"open {file_path}")
        
    except FileNotFoundError:
        print("Error: git command not found. Please ensure git is installed and in your PATH.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == '__main__':
    main()



