import sa.kps.driver.*;
import java.util.*;

public class BrandBellPolicyTest {
    static int cases;
    static void check(boolean ok){cases++;if(!ok)throw new AssertionError("case "+cases);}
    static Map<String,Set<String>> inbox(String... brands){Map<String,Set<String>> result=new HashMap<>();for(String brand:brands)result.put(brand,Set.of("same-task-id"));return result;}
    public static void main(String[] args)throws Exception{
        BrandBellPolicy p=new BrandBellPolicy();
        p.update(inbox("kps","enbishan"),0,0,30,60);
        check(p.count()==2);check(p.active().equals(Set.of("kps","enbishan")));
        check(new HashSet<>(p.due(0)).equals(Set.of("kps","enbishan")));
        check(p.due(1000).isEmpty());
        p.update(inbox("enbishan"),30000,0,30,60);
        check(p.count()==1);check(p.due(30000).equals(List.of("enbishan")));
        p.update(inbox("enbishan","kps"),60000,0,30,60);
        check(new HashSet<>(p.due(60000)).equals(Set.of("kps","enbishan")));
        p.invalidate();check(p.due(90000).isEmpty());
        p.update(inbox("kps"),90000,0,30,60);check(p.due(90000).equals(List.of("kps")));
        p.update(inbox(),120000,0,30,60);check(p.count()==0);check(p.active().isEmpty());check(p.due(120000).isEmpty());
        p.clear();check(p.count()==0);
        byte[] kps=ApprovedOrderRing.bytes("kps"),en=ApprovedOrderRing.bytes("enbishan");
        check(SoundPolicy.wave(kps));check(SoundPolicy.wave(en));check(!Arrays.equals(kps,en));
        check(SoundPolicy.digest(kps).equals("e31f18ed4c2a3ab8acf0950adebc50dade1046e94f70d9eeb1d86e7196dc577c"));
        String hash=SoundPolicy.digest(en);
        check(hash.equals("1c7d03a750534c1b48cab17d36d596049d243d226410784d8f1a15fa3c8337e5"));
        String path="/api/operations/driver-order-sound/brands/enbishan/audio/"+hash;
        check(SoundPolicy.descriptor(hash,en.length,path,"enbishan"));
        check(!SoundPolicy.descriptor(hash,en.length,path,"kps"));
        check(!SoundPolicy.descriptor(hash,en.length,path+"?brand_id=kps","enbishan"));
        check(!SoundPolicy.brand("../kps"));check(!SoundPolicy.brand("kps/other"));
        System.out.println("BrandBellPolicy: "+cases+" PASS; independent clocks, same IDs, distinct exact waveforms, scope-safe descriptors");
    }
}
