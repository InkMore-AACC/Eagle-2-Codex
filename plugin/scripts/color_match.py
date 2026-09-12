"""sRGB -> Oklab perceptual palette search; uses Eagle's palette proportions."""
import math,re
SWATCHES={'red':'#e75757','orange':'#e79342','yellow':'#edce53','green':'#60a65a','cyan':'#55bdc4','blue':'#477cdf','purple':'#9363ba','pink':'#dd77b5','white':'#eeeeee','gray':'#888888','black':'#191919'}
def lab(rgb):
 v=[c/255 for c in rgb];r,g,b=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in v]
 l=(.4122214708*r+.5363325363*g+.0514459929*b)**(1/3)
 m=(.2119034982*r+.6806995451*g+.1073969566*b)**(1/3)
 s=(.0883024619*r+.2817188376*g+.6299787005*b)**(1/3)
 return (.2104542553*l+.793617785*m-.0040720468*s,1.9779984951*l-2.428592205*m+.4505937099*s,.0259040371*l+.7827717662*m-.808675766*s)
def target(value):
 code=SWATCHES.get(value,value)
 if not re.fullmatch(r'#[0-9a-fA-F]{6}',code):raise ValueError('颜色须为预设颜色或 #RRGGBB')
 return lab([int(code[n:n+2],16) for n in (1,3,5)])
def distance(a,b):return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))
def coverage(palette,t,tolerance):
 # Sum neighboring shades rather than dropping individual small swatches.
 return sum(p['ratio'] for p in palette if distance(p['lab'],t)<=tolerance)
def matches(palette,targets,tolerance,minimum):
 return any(coverage(palette,t,tolerance)>=minimum for t in targets)

def score(palette,targets,tolerance):
 return max((sum(p["ratio"]*max(0,1-distance(p["lab"],t)/tolerance) for p in palette) for t in targets),default=0)
