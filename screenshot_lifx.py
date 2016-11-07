import os
import sys
import struct
import time

# easy_install click pyobjc lifxlan rumps

import click

import Quartz
import LaunchServices
import Cocoa
import Quartz.CoreGraphics as CG
from AppKit import NSMakeRect, NSColor, NSBundle

from lifxlan import LifxLAN

import rumps


def get_widest_display():
    total = 10
    (_, active_displays, _) = CG.CGGetActiveDisplayList(total, None, None)

    return max(
        active_displays,
        key=lambda display: CG.CGDisplayScreenSize(display).width)


def drawImageToFile(image, path):
    dpi = 72 # FIXME: Should query this from somewhere, e.g for retina displays
    url = Cocoa.NSURL.fileURLWithPath_(path)
    dest = Quartz.CGImageDestinationCreateWithURL(url, LaunchServices.kUTTypePNG, 1, None)
    properties = {
        Quartz.kCGImagePropertyDPIWidth: dpi,
        Quartz.kCGImagePropertyDPIHeight: dpi,
    }
    Quartz.CGImageDestinationAddImage(dest, image, properties)
    Quartz.CGImageDestinationFinalize(dest)
    print('Written to %s' % path)


def average_color_from_screenshot(path =  None, path2 = None, region = None):
    if region is None:
        region = CG.CGRectInfinite

    # Create screenshot as CGImage
    image = CG.CGDisplayCreateImage(get_widest_display())

    height = CG.CGImageGetHeight(image)
    width = CG.CGImageGetWidth(image)

    if path:
        drawImageToFile(image, path)

    # Create smaller image2 from captured image
    width2 = 1
    height2 = 1
    context = CG.CGBitmapContextCreate(None,
        width2, height2, 8, width2 * 4,
        CG.CGColorSpaceCreateDeviceRGB(), 1)

    CG.CGContextScaleCTM(context, float(width2)/width, float(height2)/height)
    CG.CGContextSetInterpolationQuality(context, CG.kCGInterpolationHigh)

    CG.CGContextDrawImage(context, NSMakeRect(0,0,width,height), image)
    image2 = CG.CGBitmapContextCreateImage(context)

    if path2:
        drawImageToFile(image2, path2)

    # Extract pixel value
    prov2 = CG.CGImageGetDataProvider(image2)
    data2 = CG.CGDataProviderCopyData(prov2)
    c1 = struct.unpack_from("BBBB", data2, offset=0)

    c2 = NSColor.colorWithCalibratedRed_green_blue_alpha_(
        c1[0]/255.0, c1[1]/255.0, c1[2]/255.0, c1[3]/255.0)
    result = (c2.hueComponent(),
        c2.saturationComponent(),
        c2.brightnessComponent())

    return result


class LifxProjectionStatusBarApp(rumps.App):
    ICON = 'eye.png'

    def __init__(self, **kwargs):
        menu = [
            'Enable/disable LIFX color projection',
            'Status',
        ]
        # click provides us with ability to set command line options,
        # here we convert those options into UI elements in the menu. Pretty cool!
        self.opts = kwargs
        def set_opt_from_ui(opt):
            def inner(_):
                previous_value = str(self.opts[opt.name])
                response = rumps.Window(
                    "Set value for '%s'..." % (opt.name),
                    opt.help,
                    dimensions=(200, 20),
                    default_text=previous_value).run()
                if response.text:
                    self.opts[opt.name] = opt.type(response.text)
            return inner

        for opt in main.params:
            menu.append(
                rumps.MenuItem('Preference: set %s' % opt.name,
                    callback=set_opt_from_ui(opt)),
            )

        use_icon = self.ICON if os.path.exists(self.ICON) else None
        super(LifxProjectionStatusBarApp, self).__init__("LIFX", icon=use_icon, menu=menu)

        # defaults
        self.enable_lifx_projection = False
        self.lifx_found_any_lights = False

        # lifx
        self.lifx = LifxLAN(None, verbose = False)
        self.lifx.set_power_all_lights("on", rapid=False)

    def _project_screenshot_to_lifx(self):
        color = average_color_from_screenshot()
        color_scaled = (
            color[0],
            # TODO: We might want to use gamma/power instead of multiplication
            min(1.0, color[1] * self.opts['saturation']),
            min(1.0, color[2] * self.opts['brightness']),
        )
        print('Hue: %.3f Sat: %.3f Bri: %.3f' % color_scaled)

        color_lifx = [int(color_scaled[0] * 65535),
                      int(color_scaled[1] * 65535),
                      int(color_scaled[2] * 65535),
                      self.opts['temperature']]
        self.lifx.set_color_all_lights(color_lifx, duration=0.04, rapid=True)

    @rumps.clicked('Enable/disable LIFX color projection')
    def menu_enable_lifx_projection(self, sender):
        sender.state = not sender.state
        self.enable_lifx_projection = sender.state

    @rumps.timer(0.05)
    def projection_timer(self, timer):
        if self.enable_lifx_projection and self.lifx_found_any_lights:
            self._project_screenshot_to_lifx()

    @rumps.timer(10)
    def reconfiguration_timer(self, timer):
        print('Reconfiguration')
        self.lifx_found_any_lights = bool(self.lifx.get_lights())

    @rumps.clicked('Status')
    def menu_status(self, _):
        lights = []
        try:
            for light in self.lifx.get_power_all_lights():
                lights.append(str(light[0]))
        except Exception as e:
            lights.append('Caught an exception: %s' % str(e))

        status = ('Enable projection: %d\n'
            'Found any lights: %d\n'
            'Scale brightness: %.3f\n'
            'Scale saturation: %.3f\n'
            'Use temperature when changing colors: %d K\n'
            '\nList of lights (%d): \n\n%s' % (
            self.enable_lifx_projection,
            self.lifx_found_any_lights,
            self.opts['brightness'],
            self.opts['saturation'],
            self.opts['temperature'],
            len(lights),
            '\n'.join(lights) if lights else 'No lights found'))
        window = rumps.Window('', 'Status', default_text=status, dimensions=(700, 600))
        window.run()


def hideDockIcon():
    bundle = NSBundle.mainBundle()
    info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
    info['LSUIElement'] = '1'


@click.command()
@click.option('--saturation', type=float, default=1.0,
    help='Scale saturation by a constant')
@click.option('--brightness', type=float, default=1.0,
    help='Scale brightness by a constant')
@click.option('--temperature', type=int, default=5500,
    help='Temperature in Kelvins')
def main(**kwargs):
    hideDockIcon()
    LifxProjectionStatusBarApp(**kwargs).run()

if __name__ == '__main__':
    main()
