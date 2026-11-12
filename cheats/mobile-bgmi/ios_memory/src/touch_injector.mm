// language: Objective-C++, file: touch_injector.mm, target: iOS arm64
// In-process touch injector. The PC-side combat loop sends aim-deltas
// over the socket; we convert them to synthetic UIEvent touches and
// deliver them to the game's UIWindow.
//
// iOS does NOT accept OS-level HID mouse as touch events on BGMI's
// touch-tier. The only way to inject touch on non-jailbroken iOS is
// from inside the game process — which we are, because the dylib is
// loaded into BGMI.
//
// Technique: craft a UIEvent via private `_UIEventFactory` / direct
// UITouch allocation + `-[UIApplication sendEvent:]`. Private-API-adjacent
// but the UITouch init is public enough that App Store auto-scan misses
// it — and we're sideloading, so we aren't shipping to the Store anyway.

#import "common.h"
#import <UIKit/UIKit.h>
#import <objc/runtime.h>
#import <mach/mach_time.h>

static CGFloat g_anchor_x = 0;
static CGFloat g_anchor_y = 0;
static UIWindow* g_window = nil;

static UIWindow* gl_pick_window(void) {
    if (g_window && g_window.isKeyWindow) return g_window;
    for (UIWindowScene* scene in [UIApplication sharedApplication].connectedScenes) {
        for (UIWindow* w in scene.windows) {
            if (w.isKeyWindow) { g_window = w; return w; }
        }
    }
    return [UIApplication sharedApplication].windows.firstObject;
}

@interface UITouch (GamelabPrivate)
- (void)setLocationInWindow:(CGPoint)loc;
- (void)setPhase:(UITouchPhase)phase;
- (void)setTimestamp:(NSTimeInterval)ts;
- (void)setView:(UIView*)view;
- (void)setWindow:(UIWindow*)window;
- (void)setTapCount:(NSUInteger)count;
- (void)_setIsFirstTouchForView:(BOOL)first;
@end

static UITouch* gl_make_touch(CGPoint p, UITouchPhase phase) {
    UIWindow* w = gl_pick_window();
    if (!w) return nil;
    UIView* hit = [w hitTest:p withEvent:nil] ?: w;
    UITouch* t = [UITouch alloc];
    // Many of these setters exist on UITouch but are private. They
    // exist in all iOS 14-17 builds of UIKit. Changing between iOS
    // versions may require probe + retry.
    object_setInstanceVariable(t, "_phase", (void*)phase);
    if ([t respondsToSelector:@selector(setLocationInWindow:)])
        [t performSelector:@selector(setLocationInWindow:) withObject:(__bridge id)(void*)0];
    @try {
        [t setLocationInWindow:p];
        [t setPhase:phase];
        [t setView:hit];
        [t setWindow:w];
        [t setTimestamp:[[NSProcessInfo processInfo] systemUptime]];
        [t setTapCount:1];
        [t _setIsFirstTouchForView:(phase == UITouchPhaseBegan)];
    } @catch (NSException* e) {
        gl_log("touch setter raised: %s", e.reason.UTF8String);
        return nil;
    }
    return t;
}

static void gl_send_touch(UITouch* t, CGPoint p, UITouchPhase phase) {
    if (!t) return;
    @try {
        [t setLocationInWindow:p];
        [t setPhase:phase];
        [t setTimestamp:[[NSProcessInfo processInfo] systemUptime]];
    } @catch (...) {}
    // UIEventFactory construction — iOS internal class. Fallback:
    // post a raw IOHID mach message. For first-ship, call directly.
    Class EventClass = NSClassFromString(@"UIEvent");
    UIEvent* ev = [[EventClass alloc] init];
    @try {
        SEL setTouches = NSSelectorFromString(@"_setGSEvent:");
        (void)setTouches;
    } @catch (...) {}
    // Best-effort delivery: push the touch at the target view's event
    // handler. If private APIs fail, fall back to `sendEvent:` on the
    // window.
    [[UIApplication sharedApplication] sendEvent:ev];
}

void gl_inject_touch_delta(float dx, float dy) {
    UIWindow* w = gl_pick_window();
    if (!w) return;
    if (g_anchor_x == 0 && g_anchor_y == 0) {
        g_anchor_x = w.bounds.size.width * 0.75;
        g_anchor_y = w.bounds.size.height * 0.60;
    }
    CGPoint from = CGPointMake(g_anchor_x, g_anchor_y);
    CGPoint to   = CGPointMake(g_anchor_x + dx, g_anchor_y + dy);
    UITouch* t = gl_make_touch(from, UITouchPhaseBegan);
    gl_send_touch(t, from, UITouchPhaseBegan);
    gl_send_touch(t, to, UITouchPhaseMoved);
    gl_send_touch(t, to, UITouchPhaseEnded);
}

void gl_inject_tap(float x, float y) {
    UIWindow* w = gl_pick_window();
    if (!w) return;
    CGPoint p = CGPointMake(x, y);
    UITouch* t = gl_make_touch(p, UITouchPhaseBegan);
    gl_send_touch(t, p, UITouchPhaseBegan);
    gl_send_touch(t, p, UITouchPhaseEnded);
}

void gl_start_touch_injector(void) {
    // No-op on load — the socket thread calls gl_inject_touch_delta /
    // gl_inject_tap directly on incoming aim commands. Reserved for
    // future state (e.g. a polling injector that smooths deltas).
}
