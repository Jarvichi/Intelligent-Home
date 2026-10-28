#!/usr/bin/env python3
"""Intelligent Home - Jobs - AI Trust Test 0.1.3: child of the Intelligent Home - Jobs parent. Runs ONE job (trust), registers with the parent, DRY (shadow) by default. All shared logic is in jobs_child.py (copy)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_child
import trustjob

SPEC = dict(job='trust', source='ih_jobs_trust', slug='ih_jobs_trust', title='Intelligent Home - Jobs - AI Trust Test', version='0.1.3', port=8810, runner=trustjob.run)

if __name__ == '__main__':
    jobs_child.main(SPEC, extra_str=[('ai_layer_url', 'http://172.30.32.1:8801'), ('ai_layer_token', ''), ('run_hour', '3'), ('todo_entity', 'todo.claude_to_do'),
                                     ('pause_s', '4'), ('timezone', 'Europe/London')], extra_bool=[('keep_answers', False)])
