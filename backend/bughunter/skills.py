"""Reviewed, project-scoped reusable methods with human evidence attribution.

Skills are references to procedures, never automatically executable commands.
Unreviewed methods are never returned from match().
"""
from __future__ import annotations
import re
from .store import now
from .security import redact_text


def safe(value, limit=1800):
    return redact_text(str(value or '').strip())[:limit]


class SkillService:
    def __init__(self, store):
        self.store = store

    def dispatch(self, method, p):
        if method == 'skills.list':
            scope = p.get('project_id')
            return [r for r in self.store.list('reviewed_skill') if r.get('project_id') in (None, scope)]
        if method == 'skills.get':
            return self._get(p['id'])
        if method == 'skills.propose':
            return self.propose(**p)
        if method == 'skills.review':
            return self.review(**p)
        if method == 'skills.match':
            return self.match(**p)
        raise ValueError('Unknown method-library operation.')

    def _get(self, id):
        row = self.store.get('reviewed_skill', str(id))
        if not row:raise ValueError('Method not found.')
        return row

    def propose(self, title, method, evidence_id, project_id=None, tags=None):
        if not isinstance(title, str) or not 4 <= len(title.strip()) <= 120:
            raise ValueError('Enter a method title (4–120 characters).')
        if not isinstance(method, str) or not 12 <= len(method.strip()) <= 8000:
            raise ValueError('Describe the method (12–8,000 characters).')
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError('Link a recorded task, finding or source as evidence.')
        if project_id and not self.store.get('project', project_id):
            raise ValueError('Unknown project.')
        evidence = next((self.store.get(kind, evidence_id) for kind in ('work_task', 'finding', 'source')
                         if self.store.get(kind, evidence_id)), None)
        if not evidence:
            raise ValueError('Evidence ID must reference a saved task, finding, or source.')
        if evidence.get('project_id') not in (None, project_id):
            raise ValueError('The evidence belongs to a different project.')
        tags = tags if isinstance(tags,list) else []
        tags = [safe(t, 40).lower() for t in tags[:8] if isinstance(t,str) and t.strip()]
        return self.store.put('reviewed_skill', {'title':safe(title,120),'method':safe(method,8000),
            'evidence_id':evidence_id,'project_id':project_id,'tags':tags,
            'status':'candidate','review_note':'','verified_at':None})

    def review(self, id, verified=False, note=''):
        row = self._get(id)
        if type(verified) is not bool or not isinstance(note,str) or len(note)>2000:
            raise ValueError('Invalid review decision or note.')
        if verified and len(note.strip()) < 12:
            raise ValueError('Describe how the method was checked before marking it verified.')
        return self.store.put('reviewed_skill', {**row,'status':'verified' if verified else 'rejected',
             'review_note':safe(note,2000),'verified_at':now() if verified else None},id=id)

    def match(self, query, project_id=None, limit=5):
        if not isinstance(query,str) or len(query)>5000:
            raise ValueError('Invalid search.')
        tokens = set(re.findall(r'[a-z0-9]{3,}',query.lower()))
        rows = self.store.list('reviewed_skill')
        results=[]
        for row in rows:
            if row.get('status')!='verified' or row.get('project_id') not in (None,project_id):
                continue
            haystack=(row['title']+' '+' '.join(row.get('tags',[]))+' '+row['method'][:300]).lower()
            score=sum(t in haystack for t in tokens)
            if score:
                results.append((score,row))
        return [row for _,row in sorted(results,key=lambda sr:(-sr[0],sr[1]['title']))[:min(max(int(limit),1),20)]]
