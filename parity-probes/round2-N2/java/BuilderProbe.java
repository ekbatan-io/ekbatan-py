package nprobe;

import io.ekbatan.core.domain.GenericState;
import io.ekbatan.core.domain.Id;
import io.ekbatan.core.domain.Model;
import io.ekbatan.core.internal.Validate;
import io.ekbatan.core.processor.AutoBuilder;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.Callable;

/**
 * Area N2 twin probe (round 2): generated-builder corners area G did not run. Compiled with
 * javac -processorpath (the real ekbatan-annotation-processor), then run. Twin: builder_probe.py.
 */
public class BuilderProbe {

    @AutoBuilder
    public static final class Wallet extends Model<Wallet, Id<Wallet>, GenericState> {
        public final UUID ownerId;
        public final BigDecimal balance;
        public final String memo;
        /** Derived: computed here, never passed in. Python's twin is field(init=False). */
        public final String label;

        Wallet(WalletBuilder b) {
            super(b);
            this.ownerId = Validate.notNull(b.ownerId, "ownerId cannot be null");
            this.balance = Validate.notNull(b.balance, "balance cannot be null");
            this.memo = b.memo;
            this.label = "wallet";
        }

        @Override
        public WalletBuilder copy() {
            return WalletBuilder.wallet().copyBase(this).ownerId(ownerId).balance(balance).memo(memo);
        }
    }

    @AutoBuilder
    public static final class HTTPRequest extends Model<HTTPRequest, Id<HTTPRequest>, GenericState> {
        public final String path;

        HTTPRequest(HTTPRequestBuilder b) {
            super(b);
            this.path = b.path;
        }

        @Override
        public HTTPRequestBuilder copy() {
            return HTTPRequestBuilder.hTTPRequest().copyBase(this).path(path);
        }
    }

    @AutoBuilder
    public static final class Box<T> extends Model<Box<T>, Id<Box<T>>, GenericState> {
        public final T content;

        Box(BoxBuilder<T> b) {
            super(b);
            this.content = b.content;
        }

        @Override
        public BoxBuilder<T> copy() {
            return BoxBuilder.<T>box().copyBase(this).content(content);
        }
    }

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + value);
    }

    static String err(Callable<Object> fn) {
        try {
            return "OK " + fn.call();
        } catch (Throwable e) {
            return "ERR " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
    }

    static final String ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479";
    static final UUID OWNER = UUID.fromString("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b");
    static final Instant T0 = Instant.parse("2025-01-01T00:00:00Z");

    static WalletBuilder full() {
        return WalletBuilder.wallet()
                .id(Id.of(Wallet.class, ID))
                .state(GenericState.ACTIVE)
                .createdDate(T0)
                .withInitialVersion()
                .ownerId(OWNER)
                .balance(new BigDecimal("1.00"));
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    public static void main(String[] args) throws Exception {
        p("builder.factoryName", WalletBuilder.class.getMethod("wallet").getName());
        p("builder.factoryNameAcronym", HTTPRequestBuilder.class.getMethod("hTTPRequest").getName());
        p("builder.getterAfterSet", err(() -> WalletBuilder.wallet().balance(new BigDecimal("1.00")).balance()));
        p("builder.factoryFromSource", "n/a (wallet() takes no source; copy() or copyBase(model) does it)");
        // label has a generated setter, because every instance field gets one; the constructor ignores it.
        p("builder.derivedFieldSetter", err(() -> {
            WalletBuilder b = full();
            WalletBuilder.class.getMethod("label", String.class).invoke(b, "x");
            return b.build().label;
        }));
        p("builder.derivedFieldBuild", err(() -> full().build().label));
        p("builder.copyModelWithDerivedField", err(() -> full().build().copy().build().label));
        p("builder.wrongTypeAtRuntime", "n/a (compile error)");
        p("builder.unknownSetter", "n/a (compile error)");
        Class<Box<String>> boxClass = (Class) Box.class;
        p("builder.generic.build", err(() -> BoxBuilder.<String>box()
                .id(Id.of(boxClass, ID))
                .state(GenericState.ACTIVE)
                .createdDate(T0)
                .withInitialVersion()
                .content("hello")
                .build()
                .content));
        p("builder.generic.builderIsGeneric", BoxBuilder.class.getTypeParameters().length > 0);
        p("builder.subclass.generated", "n/a (compile error, see builder.subclass.java.out)");
        p("builder.builderClassFinal", java.lang.reflect.Modifier.isFinal(WalletBuilder.class.getModifiers()));
        p("builder.builderConstructorPublic", WalletBuilder.class.getDeclaredConstructors()[0].canAccess(null));
    }
}
