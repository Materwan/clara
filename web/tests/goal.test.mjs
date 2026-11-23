// `/goal` on the page (src/lib/goal.ts): which boxes are goal commands, the stored rows of a goal, the status line's words and buttons, and how stream events change it.
import assert from 'node:assert/strict';
import test from 'node:test';

import { afterEvent, choices, counts, goalArgs, goalLine, goalRunStart, isGoalCommand, startsGoal } from '../src/lib/goal.ts';

const goal = (over = {}) => ({
  condition: 'tests pass', model: 'p:m', state: 'active', reason: '', turns_done: 2, max_turns: 10, seconds_used: 252, max_seconds: 900,
  text: '', created_at: '', updated_at: '', ...over,
});

test('only the word /goal, alone or followed by a space or a line break, is a goal command', () => {
  for (const text of ['/goal', '/goal clear', '/goal\nfix it', '  /goal --turns 3 x']) assert.equal(isGoalCommand(text), true, text);
  for (const text of ['/goals', '/goalpost', 'please /goal x', '', 'goal']) assert.equal(isGoalCommand(text), false, text);
  assert.equal(goalArgs('/goal  --turns 3 fix it \n'), '--turns 3 fix it');
  assert.equal(goalArgs('/goal'), '');
});

test('a command starts a goal only when a condition follows the flags', () => {
  assert.equal(startsGoal('fix the tests'), true);
  assert.equal(startsGoal('--turns 3 --model p:m fix it'), true);
  for (const args of ['', 'clear', 'resume', '--turns 5', '--minutes=10', '--turns 5 --minutes 10']) assert.equal(startsGoal(args), false, args);
});

test('a stored goal message is a divider, whatever else is stored', () => {
  assert.equal(goalLine('Goal: tests pass'), 'Goal: tests pass');
  assert.equal(goalLine('Goal, turn 3 of 10'), 'Goal, turn 3 of 10');
  for (const shown of ['', undefined, '/review-pr 1', 'Clara ran /x', 'Goal, turn x of 10', 'My Goal: win']) assert.equal(goalLine(shown), null, String(shown));
});

test('a followed run starts at the last stored row that has its first question, and only for a goal', () => {
  const history = [
    { role: 'user', shown: 'Goal, turn 3 of 10' }, { role: 'assistant' },
    { role: 'user', shown: 'Goal, turn 3 of 10' }, { role: 'assistant' }, { role: 'user', shown: 'Goal, turn 4 of 10' },
  ];
  assert.equal(goalRunStart(history, 'Goal, turn 3 of 10'), 2);
  assert.equal(goalRunStart(history, 'Goal, turn 4 of 10'), 4);
  assert.equal(goalRunStart(history, 'Goal, turn 9 of 10'), -1);
  assert.equal(goalRunStart([{ role: 'user', shown: 'hello' }], 'hello'), -1);
});

test('the counters say the turn under way while it works, and the turns done once it has stopped', () => {
  assert.equal(counts(goal()), 'turn 3 of 10 · 4.2 of 15 min');
  assert.equal(counts(goal({ state: 'paused', turns_done: 10, seconds_used: 0 })), '10 of 10 turns · 0 of 15 min');
  assert.equal(counts(goal({ turns_done: 10 })), 'turn 10 of 10 · 4.2 of 15 min');
});

test('a paused goal offers what fits why it paused, and no Resume while an approval waits or a limit is reached', () => {
  const paused = (over) => goal({ state: 'paused', ...over });
  assert.deepEqual(choices(goal()), { resume: false, turns: false, minutes: false });
  assert.deepEqual(choices(paused({ reason: 'stopped by you' })), { resume: true, turns: false, minutes: false });
  assert.deepEqual(choices(paused({ reason: 'waiting for your approval' })), { resume: false, turns: false, minutes: false });
  assert.deepEqual(choices(paused({ reason: 'turn limit', turns_done: 10 })), { resume: false, turns: true, minutes: false });
  assert.deepEqual(choices(paused({ reason: 'time limit', seconds_used: 900 })), { resume: false, turns: false, minutes: true });
  assert.deepEqual(choices(goal({ state: 'met' })), { resume: false, turns: false, minutes: false });
});

test('goal_turn moves the counters of the goal we know, goal_state replaces it, and nothing else changes it', () => {
  const next = afterEvent(goal({ state: 'paused', reason: 'stopped by you' }), { type: 'goal_turn', turn: 4, max_turns: 12 });
  assert.equal(next.state, 'active');
  assert.equal(next.reason, '');
  assert.equal(next.turns_done, 3);
  assert.equal(next.max_turns, 12);
  const over = goal({ state: 'met', reason: 'done' });
  assert.equal(afterEvent(goal(), { type: 'goal_state', goal: over }), over);
  assert.equal(afterEvent(goal(), { type: 'goal_state', goal: null }), null);
  assert.equal(afterEvent(null, { type: 'goal_turn', turn: 1, max_turns: 3 }), null);
  const same = goal();
  assert.equal(afterEvent(same, { type: 'done' }), same);
});
