import sys
sys.path.append('/media/shares/scripting/modules/')
from bothelper import *
import json
import praw
import os
import os.path
import requests
import time
import re

script_path = f"{os.path.dirname(__file__)}/"

with open(f"{script_path}creds.json") as c:
    login = json.load(c)
reddit = praw.Reddit(client_id=login['client_id'],
            client_secret=login['client_secret'],
            user_agent=login['user_agent'],
            redirect_uri=login['redirect_uri'],
            refresh_token=login['refresh_token'])

sub_name = "DMAcademy"
abconfig = None
wiki_config = None
mod_list = None
best_comments = []
comment_file = f"{script_path}bestofcomments.txt"
with open(comment_file, "r") as file_comments:
    best_comments = file_comments.read().split('\n')

def reload_config(subreddit):
    global abconfig
    global wiki_config
    global mod_list
    abconfig = load_local_config(script_path)
    mod_list = [str(moderator) for moderator in subreddit.moderator()]
    try:
        wikipage = subreddit.wiki["aegis_bot_config"]
        wiki_config = json.loads(wikipage.content_md)
    except Exception as exc:
        log_discord(script_path + abconfig["errfile"], abconfig["webhook"], "Aegis Bot Config Error", str(exc))
        return False
    return True

def validate_comment(comment, prefix):
    reason = ""
    pfx = prefix.lower()
    if pfx == "allow":
        return reason
    punc = None
    text = comment.body.lower()
    if not pfx[-1].isalpha():
        punc = pfx[-1]
        pfx = prefix[:-1]
    if not text.startswith(pfx):
        reason = f"All top-level comments must begin with: {pfx}"
    else:
        bad_msg = f"{pfx} must be followed by a single-line summary and additional supporting information."
        srch_parm = r"\n|\r"
        if punc is not None:
            bad_msg = f"{pfx} must be followed by a single-line summary and end with a '{punc}'."
            srch_parm = rf"\{punc}"
        txt = re.search(rf"({pfx}).*?({srch_parm})", text)
        if txt is None:
            reason = f"{bad_msg}\n\nPlease ensure there is also a line break after your summary to help distinguish it from the rest of your comment."
        elif len(txt.group()) > 250 and punc is None:
            reason = f"Your {pfx} summary must be shorter than 250 characters.\n\nPlease shorten your first line and put additional details below your summary."
    return reason

def check_top_comments(subreddit):
    title_search = ""
    prefix_search = ""
    for prefix, title_list in wiki_config["allowed_comment_formats"].items():
        if prefix in wiki_config["best_of_dma"]["allowed_comment_types"]:
            if len(prefix_search) > 0:
                prefix_search += "|"
            prefix_search += prefix
            for title in title_list:
                if len(title_search) > 0:
                    title_search += " OR "
                title_search += f"title:\"{title}\""
    if len(title_search) > 0:
        submissions = subreddit.search(title_search, sort="new", time_filter="month")
        for submission in submissions:
            submission.comment_sort = "top"
            for comment in submission.comments:
                if comment.score < wiki_config["best_of_dma"]["karma_minimum"]:
                    break
                if comment.id in best_comments or comment.banned_by is not None or comment.author is None:
                    continue
                summary = re.search(rf"({prefix_search.lower()}).*?(\n)", comment.body.lower())
                if summary is not None:
                    summary_text = comment.body[:len(summary.group())]
                    post_footer = f"\n\n---\n*Author credit: u/{comment.author.name}*\n\n*Follow the link below to see the original comment and join in on the conversation:*\n\n*https://reddit.com{comment.permalink}*"
                    post_text = f"{comment.body[len(summary_text):]}{post_footer}"
                    for pfx in prefix_search.split("|"):
                        summary_text = re.sub(pfx, "", summary_text, flags=re.IGNORECASE)
                    while not summary_text[0].isalpha() and not summary_text[0].isdigit():
                        summary_text = summary_text[1:]
                    if "\n" in summary_text:
                        summary_text = summary_text[:summary_text.index("\n")]
                    post_title = f"Best of {submission.title}: {summary_text}"
                    best_submission = subreddit.submit(post_title, selftext=post_text, flair_id="44ffc34a-1c3d-11ee-82b9-be1545703775")
                    best_submission.mod.lock()
                    best_comments.append(comment.id)
                    with open(comment_file, "a") as file_comments:
                        file_comments.write(f"{comment.id}\n")

def main():
    while True:
        try:
            subreddit = reddit.subreddit(sub_name)
            if not reload_config(subreddit):
                time.sleep(30)
                continue
            log_discord(script_path + abconfig["errfile"], abconfig["webhook"], "Aegis Bot Started for DMA", "")
            i = 0
            iteration = 2
            comment_stream = None
            while True:
                if comment_stream is None:
                    comment_stream = subreddit.stream.comments(skip_existing=True, pause_after=-1)
                try:
                    for comment in comment_stream:
                        if comment is None:
                            break
                        if comment.author.name in mod_list:
                            continue
                        comment_is_old_post = True
                        for prefix, title_list in wiki_config["allowed_comment_formats"].items():
                            if comment.submission.title in title_list:
                                comment_is_old_post = False
                                if comment.parent_id.startswith("t3_"):
                                    if comment.author is None:
                                        continue
                                    reason = validate_comment(comment, prefix)
                                    if comment.banned_by is not None:
                                        if comment.banned_by == "aegis_bot":
                                            if len(reason) < 1:
                                                comment.mod.approve()
                                                comment.refresh()
                                                for reply in comment.replies:
                                                    if reply.author.name == "aegis_bot":
                                                        reply.mod.remove(spam=False)
                                        else:
                                            continue
                                    elif len(reason) > 0:
                                        comment.mod.remove(mod_note=f"{prefix} summary improperly formatted", spam=False)
                                        rem_cmt = comment.mod.send_removal_message(message=f"Your comment was improperly formatted and has been removed:\n\n{reason}\n\nPlease correct any issues and try again.", type="public")
                                        rem_cmt.mod.lock()
                                        rem_cmt.mod.ignore_reports()
                        if comment_is_old_post:
                            comment.mod.remove(mod_note="Old posts are unavailable", spam=False)
                    i += 1
                    reload_config(subreddit)
                    if i >= (int(wiki_config["best_of_dma"]["check_frequency_minutes"]) * 60) / iteration:
                        i = 0
                        check_top_comments(subreddit)
                    time.sleep(iteration)
                except Exception as exc:
                    log_discord(script_path + abconfig["errfile"], abconfig["webhook"], "Aegis Bot Error", f"Line no: {sys.exc_info()[2].tb_lineno}\n{str(exc)}")
        except Exception as exc:
            log_discord(script_path + abconfig["errfile"], abconfig["webhook"], "Aegis Bot Fatal Error", f"Line no: {sys.exc_info()[2].tb_lineno}\n{str(exc)}")
        finally:
            time.sleep(30)

if __name__ == "__main__":
    main()
