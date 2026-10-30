package sa.kps.driver;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** The owner's approved triple-burst restaurant ring, generated offline. */
public final class ApprovedOrderRing {
    public static byte[] bytes(){
        return bytes("kps");
    }
    public static byte[] bytes(String brand){
        int rate=44100,frames=rate*9;
        ByteBuffer out=ByteBuffer.allocate(44+frames*2).order(ByteOrder.LITTLE_ENDIAN);
        out.put(new byte[]{'R','I','F','F'}).putInt(36+frames*2).put(new byte[]{'W','A','V','E','f','m','t',' '});
        out.putInt(16).putShort((short)1).putShort((short)1).putInt(rate).putInt(rate*2).putShort((short)2).putShort((short)16);
        out.put(new byte[]{'d','a','t','a'}).putInt(frames*2);
        double[] samples=new double[frames];
        boolean enbishan="enbishan".equals(brand);
        double[] groups=enbishan?new double[]{0.15,2.60,5.05,7.50}:new double[]{0.15,2.35,4.55,6.75};
        double[] offsets=enbishan?new double[]{0,0.55}:new double[]{0,0.30,0.60};
        int f1=enbishan?560:740,f2=enbishan?780:960,f3=enbishan?1120:1480,speed=enbishan?20:32;
        for(double group:groups)for(double offset:offsets){
            double length=enbishan?0.38:0.22;
            for(int i=0;i<(int)(rate*length);i++){
                double t=(double)i/rate;
                double edge=Math.min(Math.min(t/0.008,1.0),(length-t)/0.015);
                double tremolo=0.72+0.28*Math.sin(2*Math.PI*speed*t);
                double signal=0.55*Math.sin(2*Math.PI*f1*t)+0.30*Math.sin(2*Math.PI*f2*t)+0.10*Math.sin(2*Math.PI*f3*t);
                samples[(int)Math.rint((group+offset)*rate)+i]+=0.72*edge*tremolo*signal;
            }
        }
        for(double sample:samples)out.putShort((short)Math.rint(sample*32767));
        return out.array();
    }
}
