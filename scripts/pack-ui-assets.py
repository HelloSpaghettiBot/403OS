#!/usr/bin/env python3
"""Bake original UI icons and licensed font atlases; no target font engine needed."""
from pathlib import Path
import math
import struct
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'ui/assets'

def texture(image, path):
    image = image.convert('RGBA')
    path.write_bytes(b'SGOTEX01' + struct.pack('<II', *image.size) + image.tobytes())

def icon(draw, name, ox, oy, scale=4):
    def line(points, width=4):
        p=[((ox+x)*scale,(oy+y)*scale) for x,y in points]
        draw.line(p, fill='white', width=width*scale, joint='curve')
        for x,y in (p[0],p[-1]):
            r=width*scale/2
            draw.ellipse((x-r,y-r,x+r,y+r),fill='white')
    def ellipse(box, width=4, fill=None):
        x,y,w,h=box
        draw.ellipse(((ox+x)*scale,(oy+y)*scale,(ox+x+w)*scale,(oy+y+h)*scale),outline='white' if not fill else None,fill=fill,width=width*scale)
    def rr(box, radius=8, width=4):
        x,y,w,h=box
        draw.rounded_rectangle(((ox+x)*scale,(oy+y)*scale,(ox+x+w)*scale,(oy+y+h)*scale),radius*scale,outline='white',width=width*scale)
    if name in ('games','moonlight'):
        line([(17,39),(24,30),(38,33),(57,33),(72,30),(79,39),(86,68),(82,75),(75,75),(64,62),(32,62),(21,75),(14,75),(10,68),(17,39)],4)
        line([(24,43),(24,56)],3); line([(18,50),(31,50)],3)
        for x,y in [(65,44),(72,51),(65,57),(58,51)]: ellipse((x-2,y-2,4,4),fill='white')
        if name=='moonlight':
            line([(28,18),(38,12),(49,10),(60,12),(70,18)],3)
            line([(36,24),(43,20),(53,20),(61,24)],3)
    elif name=='emulators':
        rr((12,27,72,44),8,4); line([(29,29),(29,68)],3); line([(67,29),(67,68)],3)
        line([(21,40),(21,56)],3); line([(16,48),(26,48)],3)
        for x,y in [(75,39),(79,47),(73,53)]: ellipse((x-2,y-2,4,4),fill='white')
    elif name=='browser':
        ellipse((16,15,64,64)); ellipse((32,15,32,64),3)
        line([(18,48),(78,48)],3); line([(23,30),(73,30)],3); line([(23,65),(73,65)],3)
    elif name=='tailscale':
        for y in range(3):
            for x in range(3): ellipse((23+x*20,23+y*20,11,11),fill=(255,255,255,255 if y==1 else 90))
    elif name=='controllers' or name=='bluetooth':
        line([(46,13),(46,81),(68,60),(30,29)],4)
        line([(46,13),(67,34),(29,71)],4)
    elif name=='settings':
        pts=[]
        for i in range(48):
            a=i*math.pi/24; r=33 if i%6 in (0,1,4,5) else 26
            pts.append((48+math.sin(a)*r,48+math.cos(a)*r))
        line(pts+[pts[0]],4); ellipse((35,35,26,26),4)
    elif name=='power':
        pts=[(48+30*math.sin(t),48-30*math.cos(t)) for t in [0.6+i*(2*math.pi-1.2)/60 for i in range(61)]]
        line(pts,5); line([(48,12),(48,45)],5)
    elif name=='wifi':
        for radius in (32,22,12):
            line([(48+radius*math.sin(t),66-radius*math.cos(t)) for t in [-0.8+i*1.6/24 for i in range(25)]],4)
        ellipse((45,66,6,6),fill='white')
    elif name=='battery':
        rr((14,30,65,35),5,3); line([(85,41),(85,54)],3)
    elif name=='cube':
        line([(48,14),(79,31),(79,65),(48,83),(17,65),(17,31),(48,14)],4)
        line([(17,31),(48,49),(79,31)],4);line([(48,49),(48,83)],4)
    elif name=='search':
        ellipse((16,15,48,48));line([(58,59),(80,81)],5)

def main():
    wallpaper=Image.open(ASSETS/'mountain-dusk.png')
    texture(ImageOps.fit(wallpaper,(1600,1066)),ASSETS/'wallpaper.sgo')
    names=['games','emulators','moonlight','browser','tailscale','controllers','settings','power','wifi','battery','cube','search']
    atlas=Image.new('RGBA',(len(names)*96*4,96*4))
    draw=ImageDraw.Draw(atlas)
    for i,name in enumerate(names): icon(draw,name,i*96,0)
    atlas=atlas.resize((len(names)*96,96),Image.Resampling.LANCZOS)
    texture(atlas,ASSETS/'icons.sgo')
    atlas.save(ASSETS/'icons-preview.png')
    systems = {'nes':('NES',(91,32,59)), 'snes':('SUPER NINTENDO',(68,47,106)),
               'gb':('GAME BOY',(54,82,65)), 'gbc':('GAME BOY COLOR',(73,39,113)),
               'gba':('GAME BOY ADVANCE',(64,39,133)), 'genesis':('MEGA DRIVE',(37,39,51)),
               'ps1':('PLAYSTATION',(43,77,109)), 'n64':('NINTENDO 64',(35,67,116)),
               'nds':('NINTENDO DS',(38,77,92)), 'dreamcast':('DREAMCAST',(128,57,35)),
               'psp':('PSP',(46,57,79)), 'gamecube':('GAMECUBE',(65,40,130))}
    title_font=ImageFont.truetype(str(ASSETS/'fonts/Cantarell-Bold.otf'),32)
    small_font=ImageFont.truetype(str(ASSETS/'fonts/Cantarell-Regular.otf'),18)
    for system,(label,palette) in systems.items():
        poster=Image.new('RGBA',(400,480));d=ImageDraw.Draw(poster)
        for y in range(480):
            t=y/480
            col=tuple(int(v*(1-t*0.65)) for v in palette)+(255,)
            d.line((0,y,400,y),fill=col)
        for x in range(-300,700,70): d.line((x,0,x+450,480),fill=(255,255,255,12),width=2)
        # Original device silhouettes identify the platform without copied system artwork.
        if system in ('gb','gbc'):
            d.rounded_rectangle((115,62,285,312),radius=20,fill=(188,194,196),outline=(240,240,255),width=3)
            d.rounded_rectangle((132,85,268,200),radius=7,fill=(34,43,48))
            d.rectangle((146,96,254,184),fill=(126,151,115))
            d.line((143,238,182,238),fill=(37,41,53),width=13);d.line((163,218,163,258),fill=(37,41,53),width=13)
            for x,y in ((235,238),(258,222)):d.ellipse((x-10,y-10,x+10,y+10),fill=(120,36,90))
        elif system=='nds':
            for y in (60,192):
                d.rounded_rectangle((84,y,316,y+125),radius=16,fill=(206,213,226),outline=(245,247,255),width=3)
                d.rounded_rectangle((119,y+13,281,y+109),radius=6,fill=(27,35,58))
            d.line((104,244,128,244),fill=(52,57,74),width=6);d.line((116,232,116,256),fill=(52,57,74),width=6)
        elif system in ('gba','psp'):
            d.rounded_rectangle((48,122,352,284),radius=52,fill=(127,99,203) if system=='gba' else (37,41,53),outline=(204,199,244),width=4)
            d.rounded_rectangle((118,142,282,261),radius=8,fill=(18,26,47),outline=(175,174,198),width=3)
            d.line((67,200,105,200),fill='white',width=9);d.line((86,181,86,219),fill='white',width=9)
            for x,y in ((312,196),(331,213)):d.ellipse((x-7,y-7,x+7,y+7),fill=(224,224,245))
        else:
            # Console plinth and controller, with platform-specific silhouettes.
            if system=='gamecube':
                d.polygon([(116,92),(229,57),(301,102),(187,142)],fill=(159,138,220),outline=(223,212,255),width=3)
                d.polygon([(116,92),(187,142),(187,261),(116,208)],fill=(77,57,132))
                d.polygon([(187,142),(301,102),(301,223),(187,261)],fill=(116,87,180))
                d.ellipse((161,83,250,121),fill=(34,29,48))
            else:
                d.rounded_rectangle((70,108,330,225),radius=20,fill=(174,180,197) if system in ('snes','ps1') else (40,44,57),outline=(206,207,226),width=3)
                d.rounded_rectangle((111,127,287,158),radius=9,fill=(53,57,72))
                d.ellipse((265,185,279,199),fill=(121,231,199))
            d.rounded_rectangle((95,245,305,316),radius=33,fill=(195,199,220),outline=(236,236,255),width=2)
            d.line((123,278,161,278),fill=(49,52,73),width=9);d.line((142,259,142,297),fill=(49,52,73),width=9)
            for x,y in ((256,267),(275,283),(238,283),(257,299)):
                d.ellipse((x-6,y-6,x+6,y+6),fill=(122,80,180))
        d.text((200,368),label,font=title_font,anchor='mm',fill=(249,248,255))
        d.text((200,416),'EMULATOR SYSTEM',font=small_font,anchor='mm',fill=(187,183,214))
        texture(poster,ASSETS/f'system-{system}.sgo')
    for style in ('Regular','Bold'):
        font=ImageFont.truetype(str(ASSETS/f'fonts/Cantarell-{style}.otf'),48)
        image=Image.new('RGBA',(1024,1120))
        draw=ImageDraw.Draw(image)
        advances=[]
        for code in range(32,256):
            i=code-32; x=(i%16)*64; y=(i//16)*80
            draw.text((x+2,y+56),chr(code),font=font,fill='white',anchor='ls')
            advances.append(font.getlength(chr(code)))
        texture(image,ASSETS/f'font-{style.lower()}.sgo')
        (ASSETS/f'font-{style.lower()}.metrics').write_bytes(struct.pack('<224f',*advances))

if __name__=='__main__': main()
